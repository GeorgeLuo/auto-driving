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

from implementations.vehicle.chase_sim.frame_identity import (
    score_shadow_alignment_batch,
)

from .automation import _automation_dir
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
    load_latest_frame: Callable[[], dict[str, Any] | None] | None = None,
) -> CommandResult:
    """Run present/dropout/expiry/reset gates through activated memory.

    - Chase-sim (discovered): live automation frames + shadow reference alignment.
    - Offline staging ids: process-local phase script.
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

    if provider == "chase-sim":
        automation_ready = load_latest_frame is not None or _chase_automation_worker_running(
            vehicle_id
        )
        if automation_ready:
            return run_chase_shadow_memory_check(
                vehicle_id=vehicle_id,
                implementation_id=implementation_id,
                record=record,
                json_output=json_output,
                output=output,
                output_root=output_root,
                timeout_s=timeout_s,
                fresh_timeout_s=fresh_timeout_s,
                min_frames=2,
                probe_fn=probe_fn,
                reset_fn=reset_fn,
                load_latest_frame=load_latest_frame,
            )
        # Discovered Chase without a running automation worker: keep the offline
        # phase script for unit/dev use, but do not claim live shadow success.
        return run_offline_memory_check(
            vehicle_id=vehicle_id,
            provider="chase-sim",
            implementation_id=implementation_id,
            record=record,
            json_output=json_output,
            output=output,
            output_root=output_root,
            safety_note=(
                "Chase automation worker is not running; offline phase script only. "
                "Start observe-only automation for live simulator frameIndex + shadow alignment "
                "(scenario chaser-depth-obstacles)."
            ),
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


def _chase_automation_worker_running(vehicle_id: str) -> bool:
    """True only when automation state exists and status is actively running."""

    state_path = _automation_dir(vehicle_id) / "state.json"
    if not state_path.exists():
        return False
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(state, dict):
        return False
    return str(state.get("status") or "") == "running"


def run_chase_shadow_memory_check(
    *,
    vehicle_id: str,
    implementation_id: str | None = None,
    record: bool = False,
    json_output: bool = False,
    output: TextIO | None = None,
    output_root: Path | None = None,
    timeout_s: float = 3.0,
    fresh_timeout_s: float = 12.0,
    min_frames: int = 2,
    probe_fn: Callable[[], dict[str, Any]] | None = None,
    reset_fn: Callable[[], dict[str, Any]] | None = None,
    load_latest_frame: Callable[[], dict[str, Any] | None] | None = None,
) -> CommandResult:
    """Score live Chase automation frames for simulator identity + shadow alignment.

    Requires a running automation worker. Candidate cycle results must carry
    ``simulator_frame_index`` and an evaluator-only ``shadow_reference`` with the
    same index. Map/debug never enter observation or memory inputs.
    """

    selected = implementation_id or DEFAULT_MEMORY_IMPLEMENTATION
    automation_dir = _automation_dir(vehicle_id)
    latest_json_path = automation_dir / "latest_perception.json"
    state_path = automation_dir / "state.json"

    def default_load_latest() -> dict[str, Any] | None:
        if not latest_json_path.exists():
            return None
        try:
            payload = json.loads(latest_json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    load_frame = load_latest_frame or default_load_latest

    def do_probe() -> dict[str, Any]:
        if probe_fn is not None:
            return probe_fn()
        return probe_live_memory(vehicle_id=vehicle_id, timeout_s=timeout_s)

    def do_reset() -> dict[str, Any]:
        if reset_fn is not None:
            return reset_fn()
        from .memory import _reset_chase_memory

        before = do_probe()
        return _reset_chase_memory(
            vehicle_id=vehicle_id,
            before=before,
            wait_s=max(1.0, float(timeout_s)),
        )

    _emit(output, "Memory check (Chase shadow: identity → alignment → provenance → reset)")
    _emit(output, f"vehicle: {vehicle_id}")
    _emit(output, f"implementation: {selected}")
    _emit(output, "provider: chase-sim")
    _emit(output, "movement: rewritten engine observe-only (built-in model retains authority)")
    _emit(output, "")

    if not state_path.exists() and load_latest_frame is None:
        return CommandResult(
            2,
            "\n".join(
                [
                    f"No automation runtime state for {vehicle_id!r}.",
                    f"Start observe-only automation first:",
                    f"  ./cli/automa vehicles automation run --id {vehicle_id} --no-take-control",
                ]
            ),
        )

    probe = do_probe()
    if probe.get("status") not in {"live", "absent"} and load_latest_frame is None:
        return CommandResult(
            2,
            f"Chase live memory probe failed: {probe.get('error') or probe.get('status')}",
        )

    frames = collect_chase_automation_frames(
        load_latest_frame=load_frame,
        min_frames=max(2, int(min_frames)),
        timeout_s=max(1.0, float(fresh_timeout_s)),
    )
    if len(frames) < max(2, int(min_frames)):
        return CommandResult(
            2,
            "\n".join(
                [
                    f"Chase shadow check collected only {len(frames)} automation frame(s); need ≥{min_frames}.",
                    "Ensure automation is running observe-only against a live Play session",
                    f"(scenario chaser-depth-obstacles) and writing {display_path(latest_json_path)}.",
                ]
            ),
        )

    phase_results: list[dict[str, Any]] = []

    alignment = score_shadow_alignment_batch(frames, min_frames=max(2, int(min_frames)))
    phase_results.append(
        {
            "phase": "shadow_alignment",
            "passed": bool(alignment.get("passed")),
            "score": alignment,
            "frame_count": len(frames),
            "live_frame_ids": [frame.get("frame_id") for frame in frames],
            "lifecycle_source": "live_automation_worker+shadow_reference",
        }
    )
    _emit(
        output,
        f"phase: shadow_alignment  "
        f"{'PASS' if alignment.get('passed') else 'FAIL'}  "
        f"frames={alignment.get('frame_count')} aligned={alignment.get('aligned_count')}",
    )

    provenance_score = score_chase_memory_provenance(frames)
    phase_results.append(
        {
            "phase": "memory_provenance",
            "passed": bool(provenance_score.get("passed")),
            "score": provenance_score,
            "frame_count": len(frames),
            "live_frame_ids": [frame.get("frame_id") for frame in frames],
            "lifecycle_source": "live_automation_worker",
        }
    )
    _emit(
        output,
        f"phase: memory_provenance  "
        f"{'PASS' if provenance_score.get('passed') else 'FAIL'}  "
        f"{provenance_score.get('reason')}",
    )

    observe_score = score_chase_observe_only(frames)
    phase_results.append(
        {
            "phase": "observe_only",
            "passed": bool(observe_score.get("passed")),
            "score": observe_score,
            "frame_count": len(frames),
            "lifecycle_source": "live_automation_worker",
        }
    )
    _emit(
        output,
        f"phase: observe_only  "
        f"{'PASS' if observe_score.get('passed') else 'FAIL'}  "
        f"{observe_score.get('reason')}",
    )

    isolation_score = score_shadow_reference_isolation(frames)
    phase_results.append(
        {
            "phase": "shadow_isolation",
            "passed": bool(isolation_score.get("passed")),
            "score": isolation_score,
            "frame_count": len(frames),
            "lifecycle_source": "live_automation_worker",
        }
    )
    _emit(
        output,
        f"phase: shadow_isolation  "
        f"{'PASS' if isolation_score.get('passed') else 'FAIL'}  "
        f"{isolation_score.get('reason')}",
    )

    prior_epoch = str(probe.get("last_epoch_id") or "") or None
    prior_reset_count = None
    if probe.get("reset_count") is not None:
        try:
            prior_reset_count = int(probe["reset_count"])
        except (TypeError, ValueError):
            prior_reset_count = None

    try:
        reset_payload = do_reset()
    except (ConnectionError, OSError, TimeoutError, ValueError) as exc:
        return CommandResult(2, f"Chase onboard memory reset failed: {exc}")
    if not reset_payload.get("ok"):
        return CommandResult(
            2,
            f"Chase memory reset failed: {reset_payload.get('error') or reset_payload}",
        )
    after_probe = do_probe()
    reset_snapshot = chase_reset_snapshot_from_payload(reset_payload)
    reset_score = score_live_reset(
        reset_snapshot=reset_snapshot,
        prior_epoch=prior_epoch,
        prior_reset_count=prior_reset_count,
        after_probe=after_probe if isinstance(after_probe, dict) else {},
    )
    if not reset_score.get("passed"):
        # Worker result files often carry status only; allow probe transition + empty.
        reset_score = score_chase_reset_via_probe(
            prior_epoch=prior_epoch,
            prior_reset_count=prior_reset_count,
            after_probe=after_probe if isinstance(after_probe, dict) else {},
            fallback_reason=str(reset_score.get("reason") or ""),
        )

    phase_results.append(
        {
            "phase": "reset",
            "passed": bool(reset_score.get("passed")),
            "score": reset_score,
            "lifecycle_source": "live_chase_worker_reset+probe",
            "extra": {"reset": reset_payload, "after_probe": after_probe},
        }
    )
    _emit(
        output,
        f"phase: reset  "
        f"{'PASS' if reset_score.get('passed') else 'FAIL'}  "
        f"{reset_score.get('reason')}",
    )

    passed = all(bool(item.get("passed")) for item in phase_results)
    present_snapshot = {}
    for frame in reversed(frames):
        memory = frame.get("memory")
        if isinstance(memory, dict) and memory.get("records"):
            present_snapshot = memory
            break
    provenance_rows = build_memory_provenance_rows(final=present_snapshot, frames=[])

    report: dict[str, Any] = {
        "schema": MEMORY_CHECK_RESULT_SCHEMA,
        "vehicle_id": vehicle_id,
        "provider": "chase-sim",
        "implementation_id": selected,
        "activation": probe.get("activation") or "live_automation_worker",
        "passed": passed,
        "phases": [item["phase"] for item in phase_results],
        "phase_results": phase_results,
        "present_snapshot": present_snapshot,
        "final_snapshot": reset_snapshot if isinstance(reset_snapshot, dict) else {},
        "provenance_rows": provenance_rows,
        "frames_sampled": [
            {
                "frame_id": frame.get("frame_id"),
                "frame_index": frame.get("frame_index"),
                "simulator_frame_index": frame.get("simulator_frame_index"),
                "shadow_aligned": (frame.get("shadow_alignment") or {}).get("aligned")
                if isinstance(frame.get("shadow_alignment"), dict)
                else (
                    isinstance(frame.get("shadow_reference"), dict)
                    and frame.get("shadow_reference", {}).get("simulator_frame_index")
                    == frame.get("simulator_frame_index")
                ),
            }
            for frame in frames
        ],
        "safety": {
            "movement_commands_sent": False,
            "action_policy": "chase_observe_only_shadow",
            "rewritten_engine_idle": True,
            "lifecycle_source": "live_automation_worker+shadow_reference",
            "forced_dropout": False,
            "ephemeral_local_reducer": False,
            "scenario_note": (
                "Live Chase automation frames preserve simulator frameIndex and pair "
                "candidate cycle results with evaluator-only shadow_reference. Built-in "
                "model retains movement authority; rewrite stays observe-only."
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
                all_frames=[],
                phase_results=phase_results,
                output_root=output_root or memory_check_output_root(),
                captured_images=None,
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
        f"Provider: chase-sim (live shadow)",
        f"Implementation: {selected}",
        f"Frames sampled: {len(frames)}",
        f"Phases: {', '.join(item['phase'] + ('✓' if item['passed'] else '✗') for item in phase_results)}",
    ]
    return CommandResult(exit_code, "\n".join(lines))


def chase_reset_snapshot_from_payload(reset_payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize Chase worker reset result into an empty-state snapshot mapping."""

    snapshot = reset_payload.get("snapshot")
    if isinstance(snapshot, dict) and (
        snapshot.get("health") in {"empty", "unavailable"}
        or snapshot.get("record_count") == 0
        or snapshot.get("records") == []
    ):
        return {
            "health": str(snapshot.get("health") or "empty"),
            "record_count": int(snapshot.get("record_count") or 0),
            "records": list(snapshot.get("records") or []),
            "epoch_id": snapshot.get("epoch_id"),
        }

    memory = reset_payload.get("memory")
    if isinstance(memory, dict):
        status = memory.get("status") if isinstance(memory.get("status"), dict) else memory
        if isinstance(status, dict):
            health = status.get("last_health") or status.get("health") or "empty"
            count = status.get("last_record_count")
            if count is None:
                count = status.get("record_count")
            try:
                count_i = int(count if count is not None else 0)
            except (TypeError, ValueError):
                count_i = 0
            return {
                "health": str(health or "empty"),
                "record_count": count_i,
                "records": [],
                "epoch_id": status.get("last_epoch_id") or status.get("epoch_id"),
            }

    return {"health": "empty", "record_count": 0, "records": [], "epoch_id": None}


def score_chase_reset_via_probe(
    *,
    prior_epoch: str | None,
    prior_reset_count: int | None,
    after_probe: dict[str, Any],
    fallback_reason: str = "",
) -> dict[str, Any]:
    after_epoch = str(after_probe.get("last_epoch_id") or "")
    epoch_ok = bool(prior_epoch) and bool(after_epoch) and after_epoch != prior_epoch
    count_ok = False
    if prior_reset_count is not None and after_probe.get("reset_count") is not None:
        try:
            count_ok = int(after_probe["reset_count"]) > int(prior_reset_count)
        except (TypeError, ValueError):
            count_ok = False
    empty_ok = after_probe.get("last_record_count") in {0, None} or after_probe.get(
        "last_health"
    ) in {"empty", "unavailable"}
    if (epoch_ok or count_ok) and empty_ok:
        return {
            "passed": True,
            "reason": "chase worker reset confirmed via live probe epoch/reset_count transition",
            "prior_epoch": prior_epoch,
            "epoch_id": after_epoch,
            "prior_reset_count": prior_reset_count,
            "reset_count": after_probe.get("reset_count"),
            "record_ids": [],
        }
    return {
        "passed": False,
        "reason": fallback_reason
        or (
            "chase reset did not confirm empty epoch transition "
            f"(prior_epoch={prior_epoch!r} after_epoch={after_epoch!r})"
        ),
        "prior_epoch": prior_epoch,
        "epoch_id": after_epoch,
        "prior_reset_count": prior_reset_count,
        "reset_count": after_probe.get("reset_count"),
        "record_ids": [],
    }


def collect_chase_automation_frames(
    *,
    load_latest_frame: Callable[[], dict[str, Any] | None],
    min_frames: int,
    timeout_s: float,
) -> list[dict[str, Any]]:
    """Poll latest automation frame until enough distinct simulator frames arrive."""

    deadline = time.monotonic() + max(1.0, float(timeout_s))
    collected: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    while time.monotonic() < deadline and len(collected) < min_frames:
        frame = load_latest_frame()
        if isinstance(frame, dict):
            frame_id = str(frame.get("frame_id") or "")
            sim_index = frame.get("simulator_frame_index")
            if sim_index is None:
                sim_index = frame.get("frame_index")
            key = frame_id or f"idx:{sim_index}"
            if key and key not in seen_ids:
                seen_ids.add(key)
                collected.append(frame)
                if len(collected) >= min_frames:
                    break
        time.sleep(0.05)
    return collected


def score_chase_memory_provenance(frames: list[dict[str, Any]]) -> dict[str, Any]:
    """Require retained memory records (when present) cite simulator frame ids."""

    frames_with_memory = 0
    mismatched: list[str] = []
    matched = 0
    for frame in frames:
        memory = frame.get("memory") if isinstance(frame.get("memory"), dict) else None
        if memory is None:
            continue
        records = memory.get("records") if isinstance(memory.get("records"), list) else []
        if not records:
            continue
        frames_with_memory += 1
        expected = str(frame.get("frame_id") or "")
        sim_index = frame.get("simulator_frame_index")
        if sim_index is None:
            sim_index = frame.get("frame_index")
        for record in records:
            if not isinstance(record, dict):
                continue
            provenance = (
                record.get("provenance") if isinstance(record.get("provenance"), dict) else {}
            )
            prov_frame = str(provenance.get("frame_id") or "")
            if not prov_frame:
                continue
            if expected and prov_frame == expected:
                matched += 1
            elif sim_index is not None and prov_frame.endswith(f"{int(sim_index):06d}"):
                matched += 1
            else:
                mismatched.append(f"{record.get('record_id')}:{prov_frame}")
    # Memory may be empty early; require either no retained records or matching provenance.
    passed = not mismatched and (frames_with_memory == 0 or matched > 0)
    return {
        "passed": passed,
        "frames_with_memory": frames_with_memory,
        "matched_provenance_records": matched,
        "mismatched": mismatched[:12],
        "reason": (
            "memory provenance uses simulator frame identity"
            if passed and matched > 0
            else (
                "no retained memory records yet (identity path still valid)"
                if passed
                else "memory provenance frame_id does not match simulator identity"
            )
        ),
    }


def score_chase_observe_only(frames: list[dict[str, Any]]) -> dict[str, Any]:
    """Rewritten cycle must not claim applied movement authority."""

    violations: list[str] = []
    for frame in frames:
        application = str(frame.get("control_application") or "")
        action_policy = str(frame.get("action_policy") or "")
        control = frame.get("control") if isinstance(frame.get("control"), dict) else {}
        if application and application not in {
            "not_applied",
            "observe_only",
            "stop_only_safety_gate",
        }:
            violations.append(f"control_application={application}")
        if action_policy and "observe" not in action_policy and action_policy not in {
            "stop_only_safety_gate",
            "idle",
            "not_applied",
        }:
            # Allow common observe-only labels; flag explicit apply policies.
            if "apply" in action_policy or action_policy == "autonomy":
                violations.append(f"action_policy={action_policy}")
        if control.get("applied") is True:
            violations.append("control.applied=true")
    passed = not violations
    return {
        "passed": passed,
        "violations": violations,
        "reason": (
            "rewritten engine remains observe-only on sampled frames"
            if passed
            else f"observe-only violations: {violations[:5]}"
        ),
    }


def score_shadow_reference_isolation(frames: list[dict[str, Any]]) -> dict[str, Any]:
    """Shadow/debug must not appear inside observation or memory record inputs."""

    leaks: list[str] = []
    for frame in frames:
        observation = frame.get("observation") if isinstance(frame.get("observation"), dict) else {}
        if "shadow_reference" in observation:
            leaks.append(f"{frame.get('frame_id')}:observation.shadow_reference")
        sensor = observation.get("sensor_snapshot")
        if isinstance(sensor, dict):
            meta = sensor.get("metadata") if isinstance(sensor.get("metadata"), dict) else {}
            if "shadow_reference" in meta:
                leaks.append(f"{frame.get('frame_id')}:observation.sensor_snapshot.metadata")
        memory = frame.get("memory") if isinstance(frame.get("memory"), dict) else {}
        records = memory.get("records") if isinstance(memory.get("records"), list) else []
        for record in records:
            if not isinstance(record, dict):
                continue
            blob = json.dumps(record, sort_keys=True, default=str)
            if "shadow_reference" in blob or "chase_shadow_reference_v0" in blob:
                leaks.append(f"{record.get('record_id')}:memory_record")
    passed = not leaks
    return {
        "passed": passed,
        "leaks": leaks[:12],
        "reason": (
            "shadow_reference stays evaluator-only (absent from observation/memory inputs)"
            if passed
            else f"shadow/debug leaked into controller inputs: {leaks[:5]}"
        ),
    }


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
    """Run lifecycle gates from a phase script (offline / non-host unit path)."""

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
    # Keys currently refreshed by the active implementation (provenance.frame_id match).
    present_observed = currently_refreshed_memory_keys(present_cap["publication"])
    prior_epoch = str(present_mem.get("epoch_id") or "") or None
    phase_results.append(
        _phase_result(
            "present",
            present_score,
            present_mem,
            live_control=present_cap["control"],
            live_frame_ids=[present_cap["frame_id"]],
            source="live_onboard_publication.memory",
            extra={"refreshed_evidence_keys": sorted(present_observed)},
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
    dropout_observed = currently_refreshed_memory_keys(dropout_cap["publication"])
    # Lifecycle keys = evidence refreshed in present but not still refreshed in dropout.
    # Always-on camera/floor keys keep matching the latest frame_id and are excluded.
    lifecycle_keys = present_observed - dropout_observed
    if not lifecycle_keys:
        return CommandResult(
            2,
            "\n".join(
                [
                    "dropout: no evidence disappeared between present and dropout observations.",
                    f"present_refreshed={sorted(present_observed)}",
                    f"dropout_refreshed={sorted(dropout_observed)}",
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
    # Empty-state evidence comes from the atomic reset response, not a later probe
    # (the always-on cycle can repopulate memory before the next publication).
    reset_snapshot = reset_payload.get("snapshot")
    if not isinstance(reset_snapshot, dict):
        return CommandResult(
            2,
            "reset: onboard reset response missing empty snapshot payload",
        )
    after_live = do_probe()
    reset_score = score_live_reset(
        reset_snapshot=reset_snapshot,
        prior_epoch=prior_epoch,
        prior_reset_count=prior_reset_count,
        after_probe=after_live if isinstance(after_live, dict) else {},
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


def currently_refreshed_memory_keys(publication: dict[str, Any]) -> set[str]:
    """Keys the active stage refreshed on this publication's frame.

    Prefer memory records whose ``provenance.frame_id`` matches the publication
    frame id — that reuses the onboard implementation's admission behavior
    (including skipping explicit-false signals) instead of re-deriving keys in
    the CLI. Falls back to filtered observation keys when records are absent.
    """

    frame = publication.get("frame") if isinstance(publication.get("frame"), dict) else {}
    frame_id = str(frame.get("frame_id") or "").strip()
    memory = live_memory_from_publication(publication)
    records = memory.get("records") if isinstance(memory, dict) else None
    if frame_id and isinstance(records, list):
        keys: set[str] = set()
        for record in records:
            if not isinstance(record, dict):
                continue
            provenance = (
                record.get("provenance") if isinstance(record.get("provenance"), dict) else {}
            )
            if str(provenance.get("frame_id") or "").strip() != frame_id:
                continue
            record_id = str(record.get("record_id") or "").strip()
            if record_id:
                keys.add(record_id)
        return keys
    return observation_evidence_keys(publication)


def observation_evidence_keys(publication: dict[str, Any]) -> set[str]:
    """Fallback: ledger-style keys from the current observation/perception.

    Matches BoundedEvidenceLedger admission for signals: explicit ``False``
    values are not treated as currently present evidence.
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
        # Mirror ledger: skip explicit false / absent signals.
        if signal.get("value") is False:
            continue
        signal_id = str(signal.get("signal_id") or "").strip()
        if signal_id:
            keys.add(f"signal:{signal_id}")
    return keys


def score_live_reset(
    *,
    reset_snapshot: dict[str, Any],
    prior_epoch: str | None,
    prior_reset_count: int | None,
    after_probe: dict[str, Any],
) -> dict[str, Any]:
    """Score empty-state from the atomic reset snapshot; transition from probe.

    The always-on cycle may repopulate memory before a later probe, so the probe
    is used only for epoch/reset_count transition — not emptiness.
    """

    records = (
        reset_snapshot.get("records") if isinstance(reset_snapshot.get("records"), list) else []
    )
    try:
        count = int(reset_snapshot.get("record_count") if reset_snapshot.get("record_count") is not None else len(records))
    except (TypeError, ValueError):
        count = len(records)
    health = str(reset_snapshot.get("health") or "")
    empty_ok = count == 0 and health in {"empty", "unavailable"} and not records

    snapshot_epoch = str(reset_snapshot.get("epoch_id") or "")
    after_epoch = str(after_probe.get("last_epoch_id") or snapshot_epoch or "")
    epoch_changed = bool(prior_epoch) and bool(after_epoch) and after_epoch != prior_epoch
    # Reset response epoch alone can also prove transition if probe is lagging.
    snapshot_epoch_changed = (
        bool(prior_epoch) and bool(snapshot_epoch) and snapshot_epoch != prior_epoch
    )
    reset_count = after_probe.get("reset_count")
    count_bumped = False
    if prior_reset_count is not None and reset_count is not None:
        try:
            count_bumped = int(reset_count) > int(prior_reset_count)
        except (TypeError, ValueError):
            count_bumped = False
    transition_ok = epoch_changed or snapshot_epoch_changed or count_bumped

    if not empty_ok:
        return {
            "passed": False,
            "reason": (
                "onboard reset snapshot was not empty "
                f"(health={health!r} record_count={count})"
            ),
            "prior_epoch": prior_epoch,
            "epoch_id": after_epoch or snapshot_epoch,
            "prior_reset_count": prior_reset_count,
            "reset_count": reset_count,
            "record_ids": sorted(record_ids_from_memory(reset_snapshot)),
        }
    if not transition_ok:
        return {
            "passed": False,
            "reason": (
                "reset did not show epoch_id or reset_count transition on the live host "
                f"(prior_epoch={prior_epoch!r} after_epoch={after_epoch!r} "
                f"snapshot_epoch={snapshot_epoch!r} "
                f"prior_reset_count={prior_reset_count} after_reset_count={reset_count})"
            ),
            "prior_epoch": prior_epoch,
            "epoch_id": after_epoch or snapshot_epoch,
            "prior_reset_count": prior_reset_count,
            "reset_count": reset_count,
            "record_ids": [],
        }
    return {
        "passed": True,
        "reason": (
            "onboard reset returned empty snapshot with epoch/reset_count transition "
            "(post-reset probe may already show repopulated always-on evidence)"
        ),
        "prior_epoch": prior_epoch,
        "epoch_id": after_epoch or snapshot_epoch,
        "prior_reset_count": prior_reset_count,
        "reset_count": reset_count,
        "record_ids": [],
        "post_reset_probe_record_count": after_probe.get("last_record_count"),
        "post_reset_probe_health": after_probe.get("last_health"),
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
    image_paths: dict[str, str] = {}
    frame_image_paths: dict[str, str] = {}
    if captured_images:
        frames_dir = record_dir / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        for frame_id, blob in captured_images.items():
            safe_name = safe_path_part(str(frame_id)) + ".jpg"
            path = frames_dir / safe_name
            path.write_bytes(blob)
            image_paths[str(frame_id)] = display_path(path)
            frame_image_paths[str(frame_id)] = f"frames/{safe_name}"

    extract_html = render_memory_provenance_extract_html(
        vehicle_id=vehicle_id,
        payload=extract_payload,
        frames=all_frames,
        provenance_rows=provenance_rows if isinstance(provenance_rows, list) else [],
        frame_image_paths=frame_image_paths,
    )
    extract_path = record_dir / "provenance_extract.html"
    extract_path.write_text(extract_html, encoding="utf-8")

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
    persisted_report = {
        **report,
        "recorded": True,
        "record_dir": display_path(record_dir),
        "provenance_extract": display_path(extract_path),
        "record_manifest": manifest,
    }
    (record_dir / "report.json").write_text(
        json.dumps(persisted_report, indent=2, sort_keys=True, default=str),
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
