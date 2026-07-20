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
    input_fn: Callable[[str], str] | None = None,
    fetch_publication: Callable[[str], dict[str, Any]] | None = None,
    fetch_frame: Callable[[str], tuple[bytes, dict[str, str]]] | None = None,
    fetch_matched_pair: Callable[..., dict[str, Any]] | None = None,
) -> CommandResult:
    """Run present/dropout/expiry/reset gates through activated memory.

    - Chase / offline staging ids: process-local phase script.
    - PiCar: guided stationary captures from live ``/autonomy/observation/latest``,
      then the same lifecycle gates on those live-sourced observations (plus a
      deterministic max-age jump for expiry). Never commands movement. Recorded
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
    input_fn: Callable[[str], str] | None = None,
    fetch_publication: Callable[[str], dict[str, Any]] | None = None,
    fetch_frame: Callable[[str], tuple[bytes, dict[str, str]]] | None = None,
    fetch_matched_pair: Callable[..., dict[str, Any]] | None = None,
) -> CommandResult:
    """Stationary Pi memory check against live observation publications.

    Captures present and dropout publications (never moves the car), asserts
    control stays zero, then evaluates the same present/dropout/expiry/reset
    gates on a short-max-age stage fed by those live-sourced observations.
    When recording images, publication metadata and JPEG must share the same
    frame id (verified via X-Frame-Id).
    """

    base_url = picar_base_url(vehicle)
    if not base_url:
        return CommandResult(2, f"Vehicle {vehicle_id!r} has no picar base_url connection.")

    get_publication = fetch_publication or (
        lambda url: fetch_observation_publication(url, timeout_s=timeout_s)
    )
    get_frame = fetch_frame or (lambda url: fetch_observation_frame(url, timeout_s=timeout_s))
    get_matched_pair = fetch_matched_pair or (
        lambda url, **kwargs: fetch_matched_observation_pair(url, **kwargs)
    )
    prompt = input_fn or input

    _emit(output, "Physical memory check (stationary Pi)")
    _emit(output, f"vehicle: {vehicle_id}")
    _emit(output, f"endpoint: {base_url}")
    _emit(output, "movement: never commanded (manual placement only)")
    _emit(output, "frame pairing: publication frame_id must match JPEG X-Frame-Id when recording")
    _emit(output, "")

    captured_images: dict[str, bytes] = {}
    live_samples: list[dict[str, Any]] = []
    last_frame_id: str | None = None
    pair_attempts_total = 0

    placements = (
        (
            "present",
            "Object present: place a contrasting floor-standing object in view "
            "(left/center/right). Memory should retain boundary evidence.",
        ),
        (
            "dropout",
            "Object removed / clear floor: remove the object so perception can drop "
            "boundary evidence. Retained keys should still survive briefly.",
        ),
    )

    for index, (placement, message) in enumerate(placements):
        _emit(output, f"[{index + 1}/{len(placements)}] {placement}")
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
                # Prefer matched pair so recorded JPEGs cannot drift from JSON.
                pair = get_matched_pair(
                    base_url,
                    timeout_s=timeout_s,
                    match_timeout_s=max(float(fresh_timeout_s), float(timeout_s)),
                    require_image=True,
                )
                publication = pair["publication"]
                pair_attempts_total += int(pair.get("attempts") or 1)
                frame_bytes = pair.get("frame_bytes")
                frame_id = str(pair.get("frame_id") or "")
                if not frame_id:
                    return CommandResult(2, f"{placement}: matched pair missing frame_id")
                # Ensure this capture advanced beyond the previous placement.
                if last_frame_id is not None and frame_id == last_frame_id:
                    # Retry once more for a newer matched pair.
                    pair = get_matched_pair(
                        base_url,
                        timeout_s=timeout_s,
                        match_timeout_s=max(float(fresh_timeout_s), float(timeout_s)),
                        require_image=True,
                    )
                    publication = pair["publication"]
                    pair_attempts_total += int(pair.get("attempts") or 1)
                    frame_bytes = pair.get("frame_bytes")
                    frame_id = str(pair.get("frame_id") or frame_id)
                    if frame_id == last_frame_id:
                        return CommandResult(
                            2,
                            f"{placement}: matched pair did not advance past frame_id={last_frame_id}",
                        )
                if isinstance(frame_bytes, (bytes, bytearray)) and frame_bytes:
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

        force_empty = placement == "dropout"
        check_frame = publication_to_check_frame(
            publication,
            index=index,
            force_empty=force_empty,
        )
        live_samples.append(
            {
                "placement": placement,
                "frame": check_frame,
                "publication": {
                    "health": publication.get("health"),
                    "frame_id": frame_id,
                    "control": publication.get("control"),
                    "drive_mode": publication.get("drive_mode") or publication.get("mode"),
                },
                "control": control,
                "frame_pair_matched": pair_matched,
            }
        )
        _emit(
            output,
            f"  captured frame_id={frame_id} health={publication.get('health')} "
            f"control_zero={control['control_zero']}"
            + (f" pair_matched={pair_matched}" if record else ""),
        )

    present_frames = [
        sample["frame"] for sample in live_samples if sample["placement"] == "present"
    ]
    dropout_frames = [
        sample["frame"] for sample in live_samples if sample["placement"] == "dropout"
    ]
    if not present_frames:
        return CommandResult(2, "No present-phase live frames were captured.")
    if not dropout_frames:
        return CommandResult(2, "No dropout-phase live frames were captured.")

    # Expiry uses a synthetic time jump after the last live timestamp.
    last_ts = max(int(frame.get("timestamp_ms") or 0) for frame in present_frames + dropout_frames)
    expiry_frame = {
        "frame_id": "expiry_time_jump",
        "frame_index": 99,
        "timestamp_ms": last_ts + CHECK_MAX_AGE_MS + 5_000,
        "observation": {
            "observation_id": "obs_expiry_jump",
            "created_at_ms": last_ts + CHECK_MAX_AGE_MS + 5_000,
            "sensor_snapshot": {},
            "perception_plugin_id": "lightweight_observer",
            "summary": ["synthetic max-age jump after live captures"],
            "things": [],
            "signals": [],
        },
    }
    present_control = live_samples[0]["control"]
    dropout_control = live_samples[-1]["control"]
    phases = [
        {
            "name": "present",
            "frames": present_frames,
            "live_control": present_control,
            "live_frame_ids": [str(frame.get("frame_id")) for frame in present_frames],
        },
        {
            "name": "dropout",
            "frames": dropout_frames,
            "live_control": dropout_control,
            "live_frame_ids": [str(frame.get("frame_id")) for frame in dropout_frames],
        },
        {"name": "expiry", "frames": [expiry_frame]},
        {"name": "reset", "frames": []},
    ]

    return run_offline_memory_check(
        vehicle_id=vehicle_id,
        provider="picar",
        implementation_id=implementation_id,
        record=record,
        json_output=json_output,
        output=output,
        output_root=output_root,
        phases=phases,
        captured_images=captured_images or None,
        safety_note=(
            "Stationary Pi path captures live /autonomy/observation/latest publications "
            "with control-zero assertions, then evaluates memory lifecycle gates on those "
            "live-sourced observations. When --record is set, publication JSON and JPEG "
            f"must share the same frame id (pair attempts total={pair_attempts_total}). "
            "Expiry uses a deterministic max-age time jump; reset is evaluated on the "
            "check stage. No movement commands are sent."
        ),
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
    deadline = time.monotonic() + max(0.5, float(timeout_s))
    last_error: str | None = None
    last_payload: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        try:
            payload = get_publication(base_url)
        except ConnectionError as exc:
            last_error = str(exc)
            time.sleep(0.25)
            continue
        last_payload = payload
        frame = payload.get("frame") if isinstance(payload.get("frame"), dict) else {}
        frame_id = frame.get("frame_id")
        health = payload.get("health")
        if previous_frame_id is None:
            if frame_id or health in {"unavailable", "stale", "error", "absent", "warming", "healthy"}:
                return payload
        else:
            if frame_id and frame_id != previous_frame_id:
                return payload
        time.sleep(0.25)
    if last_payload is not None:
        return last_payload
    raise TimeoutError(
        f"Timed out after {timeout_s}s waiting for a fresh onboard observation"
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
        # After time jump beyond max_age, prior keys must not remain.
        leaked = bool(present_keys.intersection(record_ids))
        passed = not leaked and count == 0 and health in {"empty", "unavailable"}
        return {
            "passed": passed,
            "reason": (
                "prior keys expired after max_age time jump"
                if passed
                else "expected empty ledger after age expiry"
            ),
            "leaked_keys": sorted(present_keys.intersection(record_ids)),
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
