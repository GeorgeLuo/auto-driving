"""Guided memory lifecycle check: present → dropout → expiry → reset."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

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
) -> CommandResult:
    """Run present/dropout/expiry/reset gates through activated memory.

    Chase-first evaluation unit: process-local scripted observations that match
    the live observation shape. The rewritten path remains idle (no control is
    commanded). Pass ``record=True`` for a bounded check report and provenance
    extract. Live Pi guided placement is intentionally out of this unit.
    """

    provider: str | None = None
    if not skip_discovery:
        discovery = discover_active_vehicles(
            timeout_s=1.0,
            include_picar=True,
            include_chase_sim=True,
            include_inactive=True,
        )
        vehicle, error = find_vehicle_by_id(discovery, vehicle_id)
        if error and vehicle is None:
            # Offline evaluation still allowed when the vehicle is only a staging id.
            _emit(output, f"discovery: {error} (continuing offline phase script)")
        elif vehicle is not None:
            provider = str(vehicle.get("provider") or "")
            if provider == "picar":
                return CommandResult(
                    2,
                    "\n".join(
                        [
                            f"Vehicle {vehicle_id!r} is a physical PiCar.",
                            "This unit lands the Chase/offline phase-script memory check.",
                            "Stationary Pi present/dropout/expiry/reset check remains a later PR.",
                        ]
                    ),
                )

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

    phases = build_default_memory_check_phases()
    phase_results: list[dict[str, Any]] = []
    all_frames: list[dict[str, Any]] = []
    prior_epoch: str | None = None
    present_keys: set[str] = set()

    _emit(output, "Memory check (present → dropout → expiry → reset)")
    _emit(output, f"vehicle: {vehicle_id}")
    _emit(output, f"implementation: {selected}")
    _emit(output, f"activation: {activation_source}")
    _emit(output, f"provider: {provider or 'offline'}")
    _emit(output, "movement: never commanded (phase-script evaluation)")
    _emit(output, "")

    for phase in phases:
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
            "snapshot": final,
        }
        phase_results.append(phase_result)
        status = "PASS" if phase_result["passed"] else "FAIL"
        _emit(
            output,
            f"  {status}  keys={phase_result['record_count']}  "
            f"health={phase_result['health']}  "
            f"reason={score.get('reason')}",
        )

    passed = all(bool(item.get("passed")) for item in phase_results)
    # Provenance rows from the present-phase snapshot (keys still attributed).
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
        "provider": provider or "offline",
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
            "action_policy": "offline_phase_script",
            "rewritten_engine_idle": True,
            "scenario_note": (
                "Chase-first unit uses camera-equivalent structured observations "
                "and the same memory stage as live hosts; live chaser-depth-obstacles "
                "sampling remains optional enrichment, not required for these gates."
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
                "source": "memory_check_default_phases",
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

    artifacts = [
        "manifest.json",
        "report.json",
        "sequence.json",
        "present_memory.json",
        "provenance_extract.html",
    ]
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
            "includes_raw_camera_images": False,
            "includes_live_host_state": False,
            "retained_evidence_labeled_as": "retained_not_current",
            "phases": report.get("phases"),
        },
        "artifacts": {name: display_path(record_dir / name) for name in artifacts},
        "notes": [
            "Recording is disabled unless --record is passed.",
            "Phase script exercises present, dropout survival, max-age expiry, and reset.",
            "Retained geometry is attributed to provenance.frame_id only.",
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
