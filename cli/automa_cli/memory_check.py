"""Guided memory lifecycle check: present → dropout → expiry → reset."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TextIO

from autonomy.decision import (
    ActivatedMemoryStage,
    DecisionFrameContext,
    Observation,
    read_memory_activation,
)
from implementations.memory import (
    DEFAULT_MEMORY_IMPLEMENTATION,
    available_memory_implementation_ids,
    build_memory_activation_payload,
)

from .memory import (
    build_memory_provenance_rows,
    memory_snapshot_digest,
    post_memory_reset,
    probe_live_memory,
    render_memory_provenance_extract_html,
)
from .paths import ROOT, display_path, safe_path_part
from .physical_observation import (
    fetch_matched_observation_pair,
    fetch_observation_frame,
    fetch_observation_publication,
    picar_base_url,
)
from .vehicles import discover_active_vehicles, find_vehicle_by_id, format_active_vehicles_snapshot


MEMORY_CHECK_RESULT_SCHEMA = "vehicle_memory_check_v0"
MEMORY_CHECK_RECORD_SCHEMA = "automa_memory_check_record_v0"

# Short max age so expiry is easy to script offline without long waits.
CHECK_MAX_AGE_MS = 1_000
CHECK_MAX_RECORDS = 16


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    message: str


def memory_check_output_root() -> Path:
    return Path(
        os.environ.get(
            "AUTOMA_MEMORY_CHECK_OUTPUT_ROOT",
            str(ROOT / "lab" / "runs" / "memory-check"),
        )
    )


def run_vehicle_memory_check(
    *,
    vehicle_id: str,
    implementation_id: str | None = None,
    record: bool = False,
    json_output: bool = False,
    output: TextIO | None = None,
    output_root: Path | None = None,
    skip_discovery: bool = False,
    auto: bool = False,
    timeout_s: float = 3.0,
    fresh_timeout_s: float = 12.0,
    expiry_timeout_s: float | None = None,
    input_fn: Callable[[str], str] | None = None,
    fetch_publication: Callable[[str], dict[str, Any]] | None = None,
    fetch_frame: Callable[[str], tuple[bytes, dict[str, str]]] | None = None,
    fetch_matched_pair: Callable[..., dict[str, Any]] | None = None,
    reset_fn: Callable[[], dict[str, Any]] | None = None,
    probe_fn: Callable[[], dict[str, Any]] | None = None,
) -> CommandResult:
    """Run present/dropout/expiry/reset gates through activated memory.

    - Chase / offline staging ids: process-local phase script.
    - PiCar: scores the **live onboard** stage via publication.memory and
      onboard reset (no forced dropout, no ephemeral local reducer). Recorded
      captures require publication/JPEG frame-id pairing.
    """

    provider: str | None = None
    vehicle: dict[str, Any] | None = None
    if not skip_discovery:
        discovery = discover_active_vehicles(
            timeout_s=max(0.5, float(timeout_s)),
            include_picar=True,
            include_chase_sim=True,
            include_inactive=True,
        )
        vehicle, error = find_vehicle_by_id(discovery, vehicle_id)
        if error and vehicle is None:
            _emit(output, f"discovery: {error} (continuing offline phase script)")
        elif vehicle is not None:
            provider = str(vehicle.get("provider") or "")

    if provider == "picar":
        return run_physical_memory_check(
            vehicle_id=vehicle_id,
            vehicle=vehicle or {},
            implementation_id=implementation_id,
            record=record,
            json_output=json_output,
            output=output,
            output_root=output_root,
            auto=auto,
            timeout_s=timeout_s,
            fresh_timeout_s=fresh_timeout_s,
            input_fn=input_fn,
            fetch_publication=fetch_publication,
            fetch_frame=fetch_frame,
            fetch_matched_pair=fetch_matched_pair,
            expiry_timeout_s=expiry_timeout_s,
            reset_fn=reset_fn,
            probe_fn=probe_fn,
        )

    return run_offline_memory_check(
        vehicle_id=vehicle_id,
        provider=provider or "offline",
        implementation_id=implementation_id,
        record=record,
        json_output=json_output,
        output=output,
        output_root=output_root,
    )


def run_offline_memory_check(
    *,
    vehicle_id: str,
    provider: str = "offline",
    implementation_id: str | None = None,
    record: bool = False,
    json_output: bool = False,
    output: TextIO | None = None,
    output_root: Path | None = None,
    phases: list[dict[str, Any]] | None = None,
    safety_note: str | None = None,
    captured_images: dict[str, bytes] | None = None,
) -> CommandResult:
    """Run lifecycle gates from a phase script (Chase / offline / Pi post-capture)."""

    selected = implementation_id or DEFAULT_MEMORY_IMPLEMENTATION
    known = available_memory_implementation_ids()
    if selected not in known:
        available = ", ".join(known) or "(none)"
        return CommandResult(
            2,
            f"Unknown memory implementation {selected!r}. Available: {available}.",
        )

    try:
        stage, activation_source = _load_check_stage(
            vehicle_id=vehicle_id,
            implementation_id=selected,
            force_ephemeral=implementation_id is not None,
        )
    except (FileNotFoundError, ValueError, TypeError, ImportError, AttributeError, OSError) as exc:
        return CommandResult(2, f"Could not load memory for check: {exc}")

    active_phases = phases if phases is not None else build_default_memory_check_phases()
    phase_results: list[dict[str, Any]] = []
    all_frames: list[dict[str, Any]] = []
    prior_epoch: str | None = None
    present_keys: set[str] = set()

    _emit(output, "Memory check (present → dropout → expiry → reset)")
    _emit(output, f"vehicle: {vehicle_id}")
    _emit(output, f"implementation: {selected}")
    _emit(output, f"activation: {activation_source}")
    _emit(output, f"provider: {provider}")
    _emit(output, "movement: never commanded")
    _emit(output, "")

    for phase in active_phases:
        name = str(phase["name"])
        _emit(output, f"phase: {name}")
        if name == "reset":
            snapshot = stage.reset()
            final = snapshot.to_dict()
            frames_for_phase: list[dict[str, Any]] = []
        else:
            frames_for_phase = list(phase.get("frames") or [])
            all_frames.extend(frames_for_phase)
            final = _feed_frames(stage, frames_for_phase)

        score = score_memory_check_phase(
            phase_name=name,
            final=final,
            present_keys=present_keys,
            prior_epoch=prior_epoch,
        )
        if name == "present" and score.get("passed"):
            present_keys = {
                str(record.get("record_id"))
                for record in (final.get("records") or [])
                if isinstance(record, dict) and record.get("record_id")
            }
        if name == "reset":
            prior_epoch = str(final.get("epoch_id") or "")
        elif prior_epoch is None:
            prior_epoch = str(final.get("epoch_id") or "")

        live_control = phase.get("live_control")
        phase_result = {
            "phase": name,
            "passed": bool(score.get("passed")),
            "score": score,
            "health": final.get("health"),
            "record_count": final.get("record_count"),
            "epoch_id": final.get("epoch_id"),
            "digest": memory_snapshot_digest(final),
            "record_ids": sorted(
                str(record.get("record_id"))
                for record in (final.get("records") or [])
                if isinstance(record, dict) and record.get("record_id")
            ),
            "frame_count": len(frames_for_phase),
            "live_control_zero": (
                None if live_control is None else bool(live_control.get("control_zero"))
            ),
            "live_frame_ids": list(phase.get("live_frame_ids") or []),
            "snapshot": final,
        }
        if live_control is not None and not live_control.get("control_zero"):
            phase_result["passed"] = False
            phase_result["score"] = {
                **score,
                "passed": False,
                "reason": "live publication control was non-zero (movement not allowed)",
                "live_control": live_control,
            }
        phase_results.append(phase_result)
        status = "PASS" if phase_result["passed"] else "FAIL"
        _emit(
            output,
            f"  {status}  keys={phase_result['record_count']}  "
            f"health={phase_result['health']}  "
            f"reason={phase_result['score'].get('reason')}",
        )

    passed = all(bool(item.get("passed")) for item in phase_results)
    present_phase = next((item for item in phase_results if item["phase"] == "present"), None)
    present_snapshot = (
        present_phase.get("snapshot")
        if isinstance(present_phase, dict) and isinstance(present_phase.get("snapshot"), dict)
        else {}
    )
    provenance_rows = build_memory_provenance_rows(final=present_snapshot, frames=all_frames)

    report: dict[str, Any] = {
        "schema": MEMORY_CHECK_RESULT_SCHEMA,
        "vehicle_id": vehicle_id,
        "provider": provider,
        "implementation_id": selected,
        "activation": activation_source,
        "passed": passed,
        "phases": ["present", "dropout", "expiry", "reset"],
        "phase_results": [
            {key: value for key, value in item.items() if key != "snapshot"}
            for item in phase_results
        ],
        "present_snapshot": present_snapshot,
        "final_snapshot": phase_results[-1]["snapshot"] if phase_results else {},
        "provenance_rows": provenance_rows,
        "safety": {
            "movement_commands_sent": False,
            "action_policy": (
                "physical_observe_only" if provider == "picar" else "offline_phase_script"
            ),
            "rewritten_engine_idle": True,
            "scenario_note": safety_note
            or (
                "Offline/Chase phase script uses camera-equivalent structured observations "
                "and the same memory stage as live hosts."
            ),
        },
        "recorded": False,
        "record_dir": None,
        "provenance_extract": None,
    }

    if record:
        try:
            record_info = write_memory_check_record(
                report=report,
                all_frames=all_frames,
                phase_results=phase_results,
                output_root=output_root or memory_check_output_root(),
                captured_images=captured_images,
            )
        except OSError as exc:
            return CommandResult(2, f"Could not write memory check record: {exc}")
        report["recorded"] = True
        report["record_dir"] = record_info["record_dir"]
        report["provenance_extract"] = record_info["provenance_extract"]
        report["record_manifest"] = record_info["manifest"]
        _emit(output, f"record: {report['record_dir']}")
        _emit(output, f"provenance extract: {report['provenance_extract']}")
    else:
        _emit(output, "record: disabled (pass --record for bounded extract)")

    exit_code = 0 if passed else 1
    if json_output:
        return CommandResult(exit_code, json.dumps(report, indent=2, sort_keys=True, default=str))
    lines = [
        f"Memory check: {vehicle_id}  {'PASS' if passed else 'FAIL'}",
        f"Implementation: {selected}",
        f"Activation: {activation_source}",
        "Phases: "
        + ", ".join(
            f"{item['phase']}={'ok' if item['passed'] else 'fail'}" for item in phase_results
        ),
        "Movement: never commanded",
    ]
    if report.get("recorded"):
        lines.append(f"Record: {report['record_dir']}")
        lines.append(f"Provenance extract: {report['provenance_extract']}")
    return CommandResult(exit_code, "\n".join(lines))


def run_physical_memory_check(
    *,
    vehicle_id: str,
    vehicle: dict[str, Any],
    implementation_id: str | None = None,
    record: bool = False,
    json_output: bool = False,
    output: TextIO | None = None,
    output_root: Path | None = None,
    auto: bool = False,
    timeout_s: float = 3.0,
    fresh_timeout_s: float = 12.0,
    expiry_timeout_s: float | None = None,
    input_fn: Callable[[str], str] | None = None,
    fetch_publication: Callable[[str], dict[str, Any]] | None = None,
    fetch_frame: Callable[[str], tuple[bytes, dict[str, str]]] | None = None,
    fetch_matched_pair: Callable[..., dict[str, Any]] | None = None,
    reset_fn: Callable[[], dict[str, Any]] | None = None,
    probe_fn: Callable[[], dict[str, Any]] | None = None,
) -> CommandResult:
    """Stationary Pi check against the **active onboard** memory stage.

    Every phase is scored from live publications / status (not an ephemeral
    local reducer). Dropout uses observed publications without forced empties.
    Expiry waits for the live stage to drop retained keys. Reset calls the
    onboard reset endpoint and requires an epoch/reset-count transition.
    """

    del implementation_id  # Pi path validates the activated onboard stage only.
    base_url = picar_base_url(vehicle)
    if not base_url:
        return CommandResult(2, f"Vehicle {vehicle_id!r} has no picar base_url connection.")

    get_publication = fetch_publication or (
        lambda url: fetch_observation_publication(url, timeout_s=timeout_s)
    )
    get_matched_pair = fetch_matched_pair or (
        lambda url, **kwargs: fetch_matched_observation_pair(url, **kwargs)
    )
    do_reset = reset_fn or (lambda: post_memory_reset(base_url, timeout_s=timeout_s))
    do_probe = probe_fn or (
        lambda: probe_live_memory(
            vehicle_id=vehicle_id,
            vehicle=vehicle,
            timeout_s=timeout_s,
        )
    )
    prompt = input_fn or input

    _emit(output, "Physical memory check (stationary Pi — live onboard stage)")
    _emit(output, f"vehicle: {vehicle_id}")
    _emit(output, f"endpoint: {base_url}")
    _emit(output, "movement: never commanded (manual placement only)")
    _emit(output, "lifecycle source: live publication.memory / onboard reset")
    if record:
        _emit(output, "frame pairing: publication frame_id must match JPEG X-Frame-Id")
    _emit(output, "")

    captured_images: dict[str, bytes] = {}
    all_frames: list[dict[str, Any]] = []
    phase_results: list[dict[str, Any]] = []
    last_frame_id: str | None = None
    pair_attempts_total = 0
    present_keys: set[str] = set()
    prior_epoch: str | None = None
    prior_reset_count: int | None = None
    implementation_id_live: str | None = None
    max_age_ms: int | None = None

    def capture(placement: str, index: int, message: str) -> dict[str, Any] | CommandResult:
        nonlocal last_frame_id, pair_attempts_total
        _emit(output, f"[{index}] {placement}")
        _emit(output, message)
        if not auto:
            try:
                prompt("Press Enter when the placement is ready (Ctrl-C to abort)... ")
            except KeyboardInterrupt:
                return CommandResult(130, "Physical memory check aborted.")
        else:
            _emit(output, "(auto mode: capturing without prompt)")

        try:
            if record:
                pair = get_matched_pair(
                    base_url,
                    timeout_s=timeout_s,
                    match_timeout_s=max(float(fresh_timeout_s), float(timeout_s)),
                    require_image=True,
                    after_frame_id=last_frame_id,
                )
                if not pair.get("matched"):
                    return CommandResult(2, f"{placement}: matched pair reported matched=false")
                publication = pair["publication"]
                pair_attempts_total += int(pair.get("attempts") or 1)
                frame_bytes = pair.get("frame_bytes")
                frame_id = str(pair.get("frame_id") or "")
                if not frame_id:
                    return CommandResult(2, f"{placement}: matched pair missing frame_id")
                if last_frame_id is not None and frame_id == last_frame_id:
                    return CommandResult(
                        2,
                        f"{placement}: matched pair did not advance past frame_id={last_frame_id}",
                    )
                if not isinstance(frame_bytes, (bytes, bytearray)) or not frame_bytes:
                    return CommandResult(
                        2,
                        f"{placement}: matched pair missing nonempty JPEG for frame_id={frame_id}",
                    )
                captured_images[frame_id] = bytes(frame_bytes)
                pair_matched = True
            else:
                publication = _wait_for_fresh_publication(
                    base_url=base_url,
                    get_publication=get_publication,
                    previous_frame_id=last_frame_id,
                    timeout_s=fresh_timeout_s,
                )
                frame_meta = (
                    publication.get("frame") if isinstance(publication.get("frame"), dict) else {}
                )
                frame_id = str(frame_meta.get("frame_id") or f"live_{placement}_{index}")
                pair_matched = False
        except (ConnectionError, TimeoutError) as exc:
            return CommandResult(2, f"Could not fetch verified onboard observation: {exc}")

        last_frame_id = frame_id or last_frame_id
        control = _publication_control(publication)
        if not control["control_zero"]:
            return CommandResult(
                2,
                "\n".join(
                    [
                        f"Live publication control is non-zero during {placement} capture.",
                        f"steering={control['steering']} throttle={control['throttle']}",
                        "Memory check never commands movement; keep drive mode manual/user.",
                    ]
                ),
            )

        # Never force empty observations — dropout must be observed from the host.
        check_frame = publication_to_check_frame(publication, index=index, force_empty=False)
        live_memory = live_memory_from_publication(publication)
        if live_memory is None:
            return CommandResult(
                2,
                f"{placement}: publication has no memory snapshot. "
                "Deploy memory activation (core+autonomy) so the onboard stage is live.",
            )
        _emit(
            output,
            f"  frame_id={frame_id} health={publication.get('health')} "
            f"memory_keys={live_memory.get('record_count')} "
            f"memory_health={live_memory.get('health')} "
            f"control_zero={control['control_zero']}"
            + (f" pair_matched={pair_matched}" if record else ""),
        )
        return {
            "placement": placement,
            "frame": check_frame,
            "frame_id": frame_id,
            "publication": publication,
            "control": control,
            "live_memory": live_memory,
            "frame_pair_matched": pair_matched,
        }

    present_cap = capture(
        "present",
        1,
        "Object present: place a contrasting floor-standing object in view. "
        "Onboard memory should retain boundary evidence.",
    )
    if isinstance(present_cap, CommandResult):
        return present_cap
    all_frames.append(present_cap["frame"])
    present_mem = present_cap["live_memory"]
    implementation_id_live = (
        str(present_mem.get("implementation_id"))
        if present_mem.get("implementation_id") is not None
        else None
    )
    bounds = present_mem.get("bounds") if isinstance(present_mem.get("bounds"), dict) else {}
    if bounds.get("max_age_ms") is not None:
        try:
            max_age_ms = int(bounds["max_age_ms"])
        except (TypeError, ValueError):
            max_age_ms = None
    present_score = score_memory_check_phase(
        phase_name="present",
        final=present_mem,
        present_keys=set(),
        prior_epoch=None,
    )
    present_observed = observation_evidence_keys(present_cap["publication"])
    prior_epoch = str(present_mem.get("epoch_id") or "") or None
    phase_results.append(
        _phase_result(
            "present",
            present_score,
            present_mem,
            live_control=present_cap["control"],
            live_frame_ids=[present_cap["frame_id"]],
            source="live_onboard_publication.memory",
            extra={"observed_evidence_keys": sorted(present_observed)},
        )
    )
    _emit_phase(output, phase_results[-1])

    dropout_cap = capture(
        "dropout",
        2,
        "Object removed / clear floor: remove the object so perception drops "
        "boundary evidence. Onboard retained keys should still survive briefly.",
    )
    if isinstance(dropout_cap, CommandResult):
        return dropout_cap
    all_frames.append(dropout_cap["frame"])
    dropout_mem = dropout_cap["live_memory"]
    dropout_observed = observation_evidence_keys(dropout_cap["publication"])
    # Lifecycle keys = evidence seen in present observation but not in dropout observation.
    # Always-refreshed camera/floor keys stay in dropout observation and are excluded.
    lifecycle_keys = present_observed - dropout_observed
    if not lifecycle_keys:
        return CommandResult(
            2,
            "\n".join(
                [
                    "dropout: no evidence disappeared between present and dropout observations.",
                    f"present_observed={sorted(present_observed)}",
                    f"dropout_observed={sorted(dropout_observed)}",
                    "Place then remove a scene object so perception drops at least one key.",
                ]
            ),
        )
    present_keys = lifecycle_keys
    dropout_score = score_memory_check_phase(
        phase_name="dropout",
        final=dropout_mem,
        present_keys=present_keys,
        prior_epoch=prior_epoch,
    )
    # Annotate which keys are under lifecycle tracking.
    dropout_score = {
        **dropout_score,
        "lifecycle_keys": sorted(lifecycle_keys),
        "present_observed_keys": sorted(present_observed),
        "dropout_observed_keys": sorted(dropout_observed),
    }
    phase_results.append(
        _phase_result(
            "dropout",
            dropout_score,
            dropout_mem,
            live_control=dropout_cap["control"],
            live_frame_ids=[dropout_cap["frame_id"]],
            source="live_onboard_publication.memory",
        )
    )
    _emit_phase(output, phase_results[-1])

    # Expiry: wait only for lifecycle keys (not always-on camera/floor evidence).
    if max_age_ms is None:
        max_age_ms = 10_000
    wait_s = float(expiry_timeout_s) if expiry_timeout_s is not None else float(max_age_ms) / 1000.0 + 8.0
    _emit(
        output,
        f"phase: expiry (waiting up to {wait_s:.1f}s for lifecycle keys to age out: "
        f"{sorted(lifecycle_keys)})",
    )
    try:
        expiry_pub, expiry_mem = wait_for_live_key_expiry(
            base_url=base_url,
            get_publication=get_publication,
            present_keys=lifecycle_keys,
            previous_frame_id=last_frame_id,
            timeout_s=wait_s,
            poll_timeout_s=fresh_timeout_s,
        )
    except TimeoutError as exc:
        return CommandResult(2, f"expiry phase failed: {exc}")
    expiry_control = _publication_control(expiry_pub)
    if not expiry_control["control_zero"]:
        return CommandResult(2, "expiry: live control became non-zero while waiting")
    expiry_frame = publication_to_check_frame(expiry_pub, index=2, force_empty=False)
    all_frames.append(expiry_frame)
    expiry_score = score_memory_check_phase(
        phase_name="expiry",
        final=expiry_mem,
        present_keys=lifecycle_keys,
        prior_epoch=prior_epoch,
    )
    expiry_score = {**expiry_score, "lifecycle_keys": sorted(lifecycle_keys)}
    phase_results.append(
        _phase_result(
            "expiry",
            expiry_score,
            expiry_mem,
            live_control=expiry_control,
            live_frame_ids=[str(expiry_frame.get("frame_id") or "")],
            source="live_onboard_publication.memory",
        )
    )
    _emit_phase(output, phase_results[-1])

    # Reset: onboard endpoint + live probe (epoch / reset_count transition).
    _emit(output, "phase: reset (POST /autonomy/memory/reset + live probe)")
    before_live = do_probe()
    prior_epoch = (
        str(before_live.get("last_epoch_id") or expiry_mem.get("epoch_id") or prior_epoch or "")
        or None
    )
    if before_live.get("reset_count") is not None:
        try:
            prior_reset_count = int(before_live["reset_count"])
        except (TypeError, ValueError):
            prior_reset_count = None
    try:
        reset_payload = do_reset()
    except (ConnectionError, ValueError, OSError) as exc:
        return CommandResult(2, f"onboard memory reset failed: {exc}")
    if not reset_payload.get("ok"):
        return CommandResult(
            2,
            f"onboard memory reset failed: {reset_payload.get('error') or reset_payload}",
        )
    after_live = do_probe()
    reset_snapshot = live_memory_from_probe(after_live)
    if reset_snapshot is None and isinstance(reset_payload.get("snapshot"), dict):
        reset_snapshot = reset_payload["snapshot"]
    if reset_snapshot is None:
        return CommandResult(2, "reset: no live memory snapshot after onboard reset")
    reset_score = score_live_reset(
        final=reset_snapshot,
        prior_epoch=prior_epoch,
        prior_reset_count=prior_reset_count,
        after_probe=after_live,
    )
    phase_results.append(
        _phase_result(
            "reset",
            reset_score,
            reset_snapshot,
            live_control=None,
            live_frame_ids=[],
            source="live_onboard_reset+probe",
            extra={"before_probe": before_live, "after_probe": after_live, "reset": reset_payload},
        )
    )
    _emit_phase(output, phase_results[-1])

    passed = all(bool(item.get("passed")) for item in phase_results)
    present_snapshot = present_mem
    provenance_rows = build_memory_provenance_rows(final=present_snapshot, frames=all_frames)
    report: dict[str, Any] = {
        "schema": MEMORY_CHECK_RESULT_SCHEMA,
        "vehicle_id": vehicle_id,
        "provider": "picar",
        "implementation_id": implementation_id_live,
        "activation": "live_onboard",
        "passed": passed,
        "phases": ["present", "dropout", "expiry", "reset"],
        "phase_results": phase_results,
        "present_snapshot": present_snapshot,
        "final_snapshot": reset_snapshot,
        "provenance_rows": provenance_rows,
        "safety": {
            "movement_commands_sent": False,
            "action_policy": "physical_observe_only",
            "rewritten_engine_idle": True,
            "lifecycle_source": "live_onboard_stage",
            "forced_dropout": False,
            "ephemeral_local_reducer": False,
            "scenario_note": (
                "Pi path scores publication.memory from the activated onboard stage for "
                "present/dropout/expiry, and POST /autonomy/memory/reset for reset. "
                f"Frame-pair attempts={pair_attempts_total}. No movement commands."
            ),
        },
        "recorded": False,
        "record_dir": None,
        "provenance_extract": None,
    }

    if record:
        try:
            record_info = write_memory_check_record(
                report=report,
                all_frames=all_frames,
                phase_results=phase_results,
                output_root=output_root or memory_check_output_root(),
                captured_images=captured_images or None,
            )
        except OSError as exc:
            return CommandResult(2, f"Could not write memory check record: {exc}")
        report["recorded"] = True
        report["record_dir"] = record_info["record_dir"]
        report["provenance_extract"] = record_info["provenance_extract"]
        report["record_manifest"] = record_info["manifest"]
        _emit(output, f"record: {report['record_dir']}")
        _emit(output, f"provenance extract: {report['provenance_extract']}")
    else:
        _emit(output, "record: disabled (pass --record for bounded extract)")

    exit_code = 0 if passed else 1
    if json_output:
        return CommandResult(exit_code, json.dumps(report, indent=2, sort_keys=True, default=str))
    lines = [
        f"Memory check: {vehicle_id}  {'PASS' if passed else 'FAIL'}",
        f"Implementation: {implementation_id_live or 'live_onboard'}",
        "Lifecycle source: live onboard stage",
        "Phases: "
        + ", ".join(
            f"{item['phase']}={'ok' if item['passed'] else 'fail'}" for item in phase_results
        ),
        "Movement: never commanded",
    ]
    if report.get("recorded"):
        lines.append(f"Record: {report['record_dir']}")
        lines.append(f"Provenance extract: {report['provenance_extract']}")
    return CommandResult(exit_code, "\n".join(lines))


def live_memory_from_publication(publication: dict[str, Any]) -> dict[str, Any] | None:
    """Extract the onboard MemorySnapshot payload from a publication."""

    memory = publication.get("memory")
    if not isinstance(memory, dict):
        return None
    # Normalize count if only records are present.
    if memory.get("record_count") is None and isinstance(memory.get("records"), list):
        memory = {**memory, "record_count": len(memory["records"])}
    return memory


def live_memory_from_probe(probe: dict[str, Any]) -> dict[str, Any] | None:
    """Build a snapshot-like dict from vehicle_memory_live_v0 probe fields."""

    if not isinstance(probe, dict):
        return None
    if probe.get("status") not in {"live", "error"} and probe.get("last_health") is None:
        # Still allow empty after reset when status is live.
        if probe.get("status") != "live":
            return None
    records: list[dict[str, Any]] = []
    # Probe does not always include full records; synthesize empty for scoring.
    count = probe.get("last_record_count")
    try:
        record_count = int(count) if count is not None else 0
    except (TypeError, ValueError):
        record_count = 0
    health = probe.get("last_health") or ("empty" if record_count == 0 else "healthy")
    return {
        "health": health,
        "record_count": record_count,
        "records": records,
        "epoch_id": probe.get("last_epoch_id"),
        "implementation_id": probe.get("implementation_id"),
        "bounds": probe.get("bounds"),
    }


def record_ids_from_memory(memory: dict[str, Any]) -> set[str]:
    records = memory.get("records") if isinstance(memory.get("records"), list) else []
    return {
        str(record.get("record_id"))
        for record in records
        if isinstance(record, dict) and record.get("record_id")
    }


def observation_evidence_keys(publication: dict[str, Any]) -> set[str]:
    """Ledger-style keys implied by the *current* observation/perception payload.

    Used to separate scene evidence that disappeared between present and dropout
    from always-refreshed camera/floor signals that never leave the current view.
    """

    observation = (
        publication.get("observation") if isinstance(publication.get("observation"), dict) else None
    )
    perception = (
        publication.get("perception") if isinstance(publication.get("perception"), dict) else {}
    )
    things: list[Any]
    signals: list[Any]
    if observation is not None:
        things = list(observation.get("things") or []) if isinstance(observation.get("things"), list) else []
        signals = (
            list(observation.get("signals") or []) if isinstance(observation.get("signals"), list) else []
        )
    else:
        things = list(perception.get("things") or []) if isinstance(perception.get("things"), list) else []
        signals = (
            list(perception.get("signals") or []) if isinstance(perception.get("signals"), list) else []
        )
    keys: set[str] = set()
    for thing in things:
        if not isinstance(thing, dict):
            continue
        thing_id = str(thing.get("thing_id") or "").strip()
        if thing_id:
            keys.add(f"thing:{thing_id}")
    for signal in signals:
        if not isinstance(signal, dict):
            continue
        signal_id = str(signal.get("signal_id") or "").strip()
        if signal_id:
            keys.add(f"signal:{signal_id}")
    return keys


def score_live_reset(
    *,
    final: dict[str, Any],
    prior_epoch: str | None,
    prior_reset_count: int | None,
    after_probe: dict[str, Any],
) -> dict[str, Any]:
    """Require empty live state and epoch or reset_count transition."""

    base = score_memory_check_phase(
        phase_name="reset",
        final=final,
        present_keys=set(),
        prior_epoch=prior_epoch,
    )
    after_epoch = str(after_probe.get("last_epoch_id") or final.get("epoch_id") or "")
    epoch_changed = bool(prior_epoch) and after_epoch and after_epoch != prior_epoch
    reset_count = after_probe.get("reset_count")
    count_bumped = False
    if prior_reset_count is not None and reset_count is not None:
        try:
            count_bumped = int(reset_count) > int(prior_reset_count)
        except (TypeError, ValueError):
            count_bumped = False
    transition_ok = epoch_changed or count_bumped
    # Must not accept empty health alone without a transition signal.
    if not transition_ok:
        return {
            "passed": False,
            "reason": (
                "reset did not show epoch_id or reset_count transition on the live host "
                f"(prior_epoch={prior_epoch!r} after_epoch={after_epoch!r} "
                f"prior_reset_count={prior_reset_count} after_reset_count={reset_count})"
            ),
            "prior_epoch": prior_epoch,
            "epoch_id": after_epoch,
            "prior_reset_count": prior_reset_count,
            "reset_count": reset_count,
        }
    if not base.get("passed"):
        return {
            **base,
            "prior_reset_count": prior_reset_count,
            "reset_count": reset_count,
            "reason": base.get("reason"),
        }
    return {
        **base,
        "passed": True,
        "reason": "onboard reset produced empty live state with epoch/reset_count transition",
        "prior_reset_count": prior_reset_count,
        "reset_count": reset_count,
    }


def wait_for_live_key_expiry(
    *,
    base_url: str,
    get_publication: Callable[[str], dict[str, Any]],
    present_keys: set[str],
    previous_frame_id: str | None,
    timeout_s: float,
    poll_timeout_s: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Poll live publications until present keys leave the onboard memory snapshot."""

    deadline = time.monotonic() + max(1.0, float(timeout_s))
    last_frame_id = previous_frame_id
    last_memory: dict[str, Any] | None = None
    last_publication: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        try:
            publication = _wait_for_fresh_publication(
                base_url=base_url,
                get_publication=get_publication,
                previous_frame_id=last_frame_id,
                timeout_s=min(2.0, float(poll_timeout_s)),
            )
        except TimeoutError:
            # Fall back to a non-advancing poll so expiry can still complete.
            try:
                publication = get_publication(base_url)
            except ConnectionError:
                time.sleep(0.2)
                continue
        last_publication = publication
        frame = publication.get("frame") if isinstance(publication.get("frame"), dict) else {}
        if frame.get("frame_id"):
            last_frame_id = str(frame.get("frame_id"))
        memory = live_memory_from_publication(publication)
        if memory is None:
            time.sleep(0.2)
            continue
        last_memory = memory
        remaining = present_keys.intersection(record_ids_from_memory(memory))
        count = int(memory.get("record_count") or 0)
        # Expiry success: present keys gone (records list empty or without those ids).
        if present_keys and not remaining:
            return publication, memory
        if not present_keys and count == 0 and str(memory.get("health") or "") in {
            "empty",
            "unavailable",
        }:
            return publication, memory
        # If records are not listed in publication, fall back to count=0 empty health.
        if (
            present_keys
            and not isinstance(memory.get("records"), list)
            and count == 0
            and str(memory.get("health") or "") in {"empty", "unavailable"}
        ):
            return publication, memory
        time.sleep(0.25)

    detail = ""
    if last_memory is not None:
        detail = (
            f" last_health={last_memory.get('health')} "
            f"last_count={last_memory.get('record_count')} "
            f"remaining={sorted(present_keys.intersection(record_ids_from_memory(last_memory)))}"
        )
    raise TimeoutError(
        f"live onboard memory did not drop present keys within {timeout_s}s.{detail}"
    )


def _phase_result(
    name: str,
    score: dict[str, Any],
    final: dict[str, Any],
    *,
    live_control: dict[str, Any] | None,
    live_frame_ids: list[str],
    source: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "phase": name,
        "passed": bool(score.get("passed")),
        "score": score,
        "health": final.get("health"),
        "record_count": final.get("record_count"),
        "epoch_id": final.get("epoch_id"),
        "digest": memory_snapshot_digest(final) if isinstance(final, dict) else None,
        "record_ids": sorted(record_ids_from_memory(final)),
        "live_control_zero": (
            None if live_control is None else bool(live_control.get("control_zero"))
        ),
        "live_frame_ids": live_frame_ids,
        "lifecycle_source": source,
        "snapshot": final,
    }
    if extra:
        payload.update(extra)
    if live_control is not None and not live_control.get("control_zero"):
        payload["passed"] = False
        payload["score"] = {
            **score,
            "passed": False,
            "reason": "live publication control was non-zero (movement not allowed)",
        }
    return payload


def _emit_phase(output: TextIO | None, phase_result: dict[str, Any]) -> None:
    status = "PASS" if phase_result.get("passed") else "FAIL"
    score = phase_result.get("score") if isinstance(phase_result.get("score"), dict) else {}
    _emit(
        output,
        f"  {status}  keys={phase_result.get('record_count')}  "
        f"health={phase_result.get('health')}  "
        f"reason={score.get('reason')}",
    )


def publication_to_check_frame(
    publication: dict[str, Any],
    *,
    index: int,
    force_empty: bool = False,
) -> dict[str, Any]:
    """Adapt a live onboard publication into a memory-check sequence frame."""

    frame = publication.get("frame") if isinstance(publication.get("frame"), dict) else {}
    perception = (
        publication.get("perception") if isinstance(publication.get("perception"), dict) else {}
    )
    observation = (
        publication.get("observation") if isinstance(publication.get("observation"), dict) else None
    )
    timestamp_ms = (
        frame.get("captured_at_ms")
        or frame.get("completed_at_ms")
        or publication.get("timestamp_ms")
        or (index + 1) * 100
    )
    frame_id = str(frame.get("frame_id") or f"live_frame_{index:03d}")
    frame_index = int(frame.get("frame_index") or index)

    if observation is None:
        things = perception.get("things") if isinstance(perception.get("things"), list) else []
        signals = perception.get("signals") if isinstance(perception.get("signals"), list) else []
        observation = {
            "observation_id": f"obs_live_{index:03d}",
            "created_at_ms": int(timestamp_ms),
            "sensor_snapshot": {},
            "perception_plugin_id": perception.get("plugin_id")
            or publication.get("algorithm")
            or "onboard_perception",
            "summary": list(perception.get("lines") or [])[:8]
            if isinstance(perception.get("lines"), list)
            else [f"live publication {frame_id}"],
            "things": [item for item in things if isinstance(item, dict)],
            "signals": [item for item in signals if isinstance(item, dict)],
        }
    else:
        observation = dict(observation)
        if observation.get("created_at_ms") is None:
            observation["created_at_ms"] = int(timestamp_ms)
        if not observation.get("observation_id"):
            observation["observation_id"] = f"obs_live_{index:03d}"

    if force_empty:
        observation = {
            **observation,
            "things": [],
            "signals": [],
            "summary": list(observation.get("summary") or []) + ["forced_empty_for_dropout_phase"],
        }

    return {
        "frame_id": frame_id,
        "frame_index": frame_index,
        "timestamp_ms": int(observation.get("created_at_ms") or timestamp_ms),
        "observation": observation,
        "source": "live_publication",
    }


def _publication_control(publication: dict[str, Any]) -> dict[str, Any]:
    control = publication.get("control") if isinstance(publication.get("control"), dict) else {}
    steering = _as_float(control.get("steering"))
    throttle = _as_float(control.get("throttle"))
    return {
        "steering": steering,
        "throttle": throttle,
        "control_zero": steering == 0.0 and throttle == 0.0,
        "mode": publication.get("drive_mode") or publication.get("mode"),
    }


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _wait_for_fresh_publication(
    *,
    base_url: str,
    get_publication: Callable[[str], dict[str, Any]],
    previous_frame_id: str | None,
    timeout_s: float,
) -> dict[str, Any]:
    """Wait for a publication whose frame_id advances past ``previous_frame_id``.

    Fails closed when no new frame arrives. Never returns a stale same-id payload
    after the deadline (that path allowed present/dropout to reuse one frame).
    """

    deadline = time.monotonic() + max(0.5, float(timeout_s))
    last_error: str | None = None
    last_frame_id: str | None = None
    previous = (previous_frame_id or "").strip() or None
    while time.monotonic() < deadline:
        try:
            payload = get_publication(base_url)
        except ConnectionError as exc:
            last_error = str(exc)
            time.sleep(0.25)
            continue
        frame = payload.get("frame") if isinstance(payload.get("frame"), dict) else {}
        frame_id = frame.get("frame_id")
        if isinstance(frame_id, str) and frame_id.strip():
            last_frame_id = frame_id.strip()
        health = payload.get("health")
        if previous is None:
            if last_frame_id or health in {
                "unavailable",
                "stale",
                "error",
                "absent",
                "warming",
                "healthy",
            }:
                return payload
        else:
            if last_frame_id and last_frame_id != previous:
                return payload
        time.sleep(0.25)
    raise TimeoutError(
        f"Timed out after {timeout_s}s waiting for a fresh onboard observation"
        + (f" after frame_id={previous!r}" if previous else "")
        + (f" (last_frame_id={last_frame_id!r})" if last_frame_id else "")
        + (f": {last_error}" if last_error else "")
    )


def build_default_memory_check_phases() -> list[dict[str, Any]]:
    """Scripted present/dropout/expiry/reset sequence for offline evaluation."""

    def frame(
        frame_id: str,
        index: int,
        timestamp_ms: int,
        *,
        things: list[dict[str, Any]] | None = None,
        signals: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return {
            "frame_id": frame_id,
            "frame_index": index,
            "timestamp_ms": timestamp_ms,
            "observation": {
                "observation_id": f"obs_{index:03d}",
                "created_at_ms": timestamp_ms,
                "sensor_snapshot": {},
                "perception_plugin_id": "lightweight_observer",
                "summary": [f"check phase frame {frame_id}"],
                "things": things or [],
                "signals": signals or [],
            },
        }

    boundary = {
        "thing_id": "floor_boundary_000",
        "kind": "floor_boundary",
        "label": "boundary",
        "confidence": 0.91,
        "location": {
            "frame": "image",
            "zone": "center",
            "bbox_xyxy_norm": [0.35, 0.45, 0.65, 0.95],
        },
        "source_plugin_id": "floor-plane-v0",
    }
    floor_signal = {
        "signal_id": "floor_visible",
        "value": True,
        "confidence": 0.96,
    }

    return [
        {
            "name": "present",
            "frames": [
                frame("present_000", 0, 100, things=[boundary], signals=[floor_signal]),
                frame("present_001", 1, 200, things=[boundary], signals=[floor_signal]),
            ],
        },
        {
            "name": "dropout",
            "frames": [
                # No things/signals: prior retained evidence should survive while young.
                frame("dropout_000", 2, 300),
                frame("dropout_001", 3, 400),
            ],
        },
        {
            "name": "expiry",
            "frames": [
                # Jump far past CHECK_MAX_AGE_MS so retained present evidence expires.
                frame("expiry_000", 4, 100 + CHECK_MAX_AGE_MS + 5_000),
            ],
        },
        {"name": "reset", "frames": []},
    ]


def score_memory_check_phase(
    *,
    phase_name: str,
    final: dict[str, Any],
    present_keys: set[str],
    prior_epoch: str | None,
) -> dict[str, Any]:
    """Score one lifecycle phase against bounded-evidence expectations."""

    records = final.get("records") if isinstance(final.get("records"), list) else []
    record_ids = {
        str(record.get("record_id"))
        for record in records
        if isinstance(record, dict) and record.get("record_id")
    }
    health = str(final.get("health") or "")
    count = int(final.get("record_count") or len(records))
    epoch = str(final.get("epoch_id") or "")

    if phase_name == "present":
        has_thing = any(rid.startswith("thing:") for rid in record_ids)
        passed = count >= 1 and has_thing and health in {"healthy", "empty"}
        # healthy expected when records exist
        if count >= 1:
            passed = health == "healthy" and has_thing
        return {
            "passed": passed,
            "reason": (
                "retained at least one thing key from present observations"
                if passed
                else "expected healthy snapshot with retained thing keys after present"
            ),
            "record_ids": sorted(record_ids),
        }

    if phase_name == "dropout":
        # BoundedEvidenceLedger keeps young keys when later observations are empty.
        survived = bool(present_keys) and present_keys.issubset(record_ids)
        passed = survived and count >= len(present_keys)
        return {
            "passed": passed,
            "reason": (
                "retained present keys through empty dropout frames"
                if passed
                else "expected present keys to survive short dropout before max_age"
            ),
            "expected_keys": sorted(present_keys),
            "record_ids": sorted(record_ids),
        }

    if phase_name == "expiry":
        # Tracked keys must leave memory. Always-on evidence may remain healthy.
        leaked = present_keys.intersection(record_ids)
        if present_keys:
            passed = not leaked
            reason = (
                "tracked lifecycle keys expired from live/offline memory"
                if passed
                else "tracked lifecycle keys still present after expiry wait"
            )
        else:
            passed = count == 0 and health in {"empty", "unavailable"}
            reason = (
                "ledger empty after age expiry"
                if passed
                else "expected empty ledger after age expiry"
            )
        return {
            "passed": passed,
            "reason": reason,
            "leaked_keys": sorted(leaked),
            "record_ids": sorted(record_ids),
        }

    if phase_name == "reset":
        epoch_changed = prior_epoch is None or epoch != prior_epoch
        passed = count == 0 and health in {"empty", "unavailable"} and epoch_changed
        return {
            "passed": passed,
            "reason": (
                "reset produced a new empty epoch"
                if passed
                else "expected empty snapshot with new epoch_id after reset"
            ),
            "prior_epoch": prior_epoch,
            "epoch_id": epoch,
            "record_ids": sorted(record_ids),
        }

    return {"passed": False, "reason": f"unknown phase {phase_name!r}"}


def write_memory_check_record(
    *,
    report: dict[str, Any],
    all_frames: list[dict[str, Any]],
    phase_results: list[dict[str, Any]],
    output_root: Path,
    captured_images: dict[str, bytes] | None = None,
) -> dict[str, Any]:
    """Write bounded check artifacts and a provenance extract from present keys."""

    vehicle_id = str(report.get("vehicle_id") or "vehicle")
    run_id = f"{safe_path_part(vehicle_id)}-{time.strftime('%Y%m%d-%H%M%S')}"
    record_dir = Path(output_root) / run_id
    record_dir.mkdir(parents=True, exist_ok=False)

    present_snapshot = (
        report.get("present_snapshot")
        if isinstance(report.get("present_snapshot"), dict)
        else {}
    )
    provenance_rows = (
        report.get("provenance_rows")
        if isinstance(report.get("provenance_rows"), list)
        else []
    )
    extract_payload = {
        "implementation_id": report.get("implementation_id"),
        "digest": memory_snapshot_digest(present_snapshot) if present_snapshot else "",
        "frame_count": len(all_frames),
        "final": present_snapshot,
        "per_frame": [
            {
                "frame_id": item.get("phase"),
                "record_count": item.get("record_count"),
                "health": item.get("health"),
            }
            for item in phase_results
        ],
    }
    extract_html = render_memory_provenance_extract_html(
        vehicle_id=vehicle_id,
        payload=extract_payload,
        frames=all_frames,
        provenance_rows=provenance_rows if isinstance(provenance_rows, list) else [],
    )

    (record_dir / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    (record_dir / "sequence.json").write_text(
        json.dumps(
            {
                "schema": "automa_memory_observation_sequence_v0",
                "source": (
                    "live_pi_publications"
                    if report.get("provider") == "picar"
                    else "memory_check_default_phases"
                ),
                "frames": all_frames,
            },
            indent=2,
            sort_keys=True,
            default=str,
        ),
        encoding="utf-8",
    )
    (record_dir / "present_memory.json").write_text(
        json.dumps(present_snapshot, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    extract_path = record_dir / "provenance_extract.html"
    extract_path.write_text(extract_html, encoding="utf-8")

    image_paths: dict[str, str] = {}
    if captured_images:
        frames_dir = record_dir / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        for frame_id, blob in captured_images.items():
            safe_name = safe_path_part(str(frame_id)) + ".jpg"
            path = frames_dir / safe_name
            path.write_bytes(blob)
            image_paths[str(frame_id)] = display_path(path)

    artifacts = [
        "manifest.json",
        "report.json",
        "sequence.json",
        "present_memory.json",
        "provenance_extract.html",
    ]
    if image_paths:
        artifacts.append("frames/")
    manifest = {
        "schema": MEMORY_CHECK_RECORD_SCHEMA,
        "run_id": run_id,
        "vehicle_id": vehicle_id,
        "created_at_ms": int(time.time() * 1000),
        "opt_in": True,
        "writes_default_history": False,
        "passed": report.get("passed"),
        "implementation_id": report.get("implementation_id"),
        "bounds": {
            "artifacts": artifacts,
            "includes_raw_camera_images": bool(image_paths),
            "includes_live_host_state": report.get("provider") == "picar",
            "retained_evidence_labeled_as": "retained_not_current",
            "phases": report.get("phases"),
            "captured_frame_images": image_paths,
        },
        "artifacts": {
            name: display_path(record_dir / name.rstrip("/"))
            for name in artifacts
        },
        "notes": [
            "Recording is disabled unless --record is passed.",
            "Phase script exercises present, dropout survival, max-age expiry, and reset.",
            "Retained geometry is attributed to provenance.frame_id only.",
            "Pi path may include exact JPEG frames captured with each live publication.",
        ],
    }
    (record_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return {
        "record_dir": display_path(record_dir),
        "provenance_extract": display_path(extract_path),
        "manifest": manifest,
    }


def _load_check_stage(
    *,
    vehicle_id: str,
    implementation_id: str,
    force_ephemeral: bool,
) -> tuple[ActivatedMemoryStage, str]:
    # Check uses fixed short max_age so expiry is deterministic offline.
    # force_ephemeral reserved for future staged-activation variants.
    del force_ephemeral
    import tempfile

    payload = build_memory_activation_payload(
        implementation_id,
        config_overrides={
            "max_records": CHECK_MAX_RECORDS,
            "max_age_ms": CHECK_MAX_AGE_MS,
            "eviction_policy": "oldest_first",
        },
    )
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".json",
        prefix=f"memory-check-{safe_path_part(vehicle_id)}-",
        delete=False,
        encoding="utf-8",
    )
    handle.write(json.dumps(payload, indent=2, sort_keys=True))
    handle.close()
    path = Path(handle.name)
    try:
        stage = ActivatedMemoryStage(read_memory_activation(path))
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    return stage, f"ephemeral-check:{implementation_id}(max_age_ms={CHECK_MAX_AGE_MS})"


def _feed_frames(stage: ActivatedMemoryStage, frames: list[dict[str, Any]]) -> dict[str, Any]:
    snapshot = stage.snapshot()
    for frame in frames:
        observation = Observation.from_dict(frame["observation"])
        context = DecisionFrameContext(
            frame_id=str(frame["frame_id"]),
            frame_index=int(frame["frame_index"]),
            timestamp_ms=int(frame["timestamp_ms"]),
        )
        snapshot = stage.update(context, observation)
    return snapshot.to_dict()


def _emit(output: TextIO | None, message: str) -> None:
    if output is not None:
        print(message, file=output, flush=True)
