from __future__ import annotations

import json
import os
import resource
import statistics
import sys
import tempfile
import time
from collections import Counter, defaultdict
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autonomy.decision_cycle.activation import step_activation_from_payload
from autonomy.decision_cycle.perception.inputs import build_perception_request
from autonomy.decision_cycle.perception.interface import (
    PERCEPTION_TEXT_SCHEMA,
    PerceptionText,
)
from autonomy.decision_cycle.perception.runner import PerceptionRunner
from autonomy.vehicle import (
    FRONT_CAMERA_SENSOR_ID,
    SensorFrame,
    SensorReading,
    SensorReadRequest,
)
from implementations.decision_cycle.catalog import selection_activation
from implementations.decision_cycle.perception.presets import PERCEPTION_PRESETS

from .inspection_runs import recorded_selection, selection_record
from .paths import ROOT, display_path, safe_path_part
from .perception import ensure_local_perception_runtime
from .perception_evaluation import evaluate_perception_frames
from .step_hosting import load_staged_runner
from .vehicle_access import create_vehicle_access
from .vehicles import (
    discover_active_vehicles,
    find_vehicle_by_id,
    format_active_vehicles,
)
from .workbench_source import (
    WORKBENCH_DEFAULT_MAX_FRAMES,
    SourceValidationError,
    normalize_image_directory,
    normalize_image_file,
    read_image_manifest,
)

DEFAULT_FRAME_COUNT = 5
DEFAULT_INTERVAL_S = 0.25
INSPECT_ROOT = Path(os.environ.get("AUTOMA_PERCEPTION_INSPECT_ROOT", ROOT / "runtime" / "perception-inspections"))
PERCEPTION_INSPECT_SCHEMA = "perception_inspect_v0"


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    message: str


def inspect_perception(
    source: Path | None = None,
    *,
    vehicle_id: str | None = None,
    frames: int | None = None,
    interval_s: float | None = None,
    timeout_s: float | None = None,
    record: bool = False,
    json_output: bool = False,
    preset: str | None = None,
    plugins: list[str] | None = None,
    max_frames: int = WORKBENCH_DEFAULT_MAX_FRAMES,
) -> CommandResult:
    """Show what a perception selection detects, from images or a live vehicle.

    With ``source`` (an image, a directory of images, or a recorded run) the
    selection is applied to those images. Without it, frames are read from an
    active vehicle. With neither ``preset`` nor ``plugins``, a recorded run's
    own selection applies to a source, and a vehicle's staged selection to a
    live read.
    """

    if preset is not None and plugins:
        return CommandResult(2, "Choose either --preset or --plugin, not both.")
    if preset is not None and preset not in PERCEPTION_PRESETS:
        return CommandResult(2, f"Unknown perception preset {preset!r}.")
    if max_frames <= 0:
        return _unreadable_source("max_frames must be greater than zero")
    if source is not None:
        live_only = {
            "--id": vehicle_id,
            "--frames": frames,
            "--interval-s": interval_s,
            "--timeout-s": timeout_s,
        }
        given = [flag for flag, value in live_only.items() if value is not None]
        if given:
            return CommandResult(
                2,
                f"{', '.join(given)} read a live vehicle and cannot be combined with a source.",
            )
        return _inspect_images(
            source,
            record=record,
            json_output=json_output,
            preset=preset,
            plugins=plugins,
            max_frames=max_frames,
        )
    return _inspect_vehicle(
        vehicle_id=vehicle_id,
        frames=DEFAULT_FRAME_COUNT if frames is None else frames,
        interval_s=DEFAULT_INTERVAL_S if interval_s is None else interval_s,
        timeout_s=3.0 if timeout_s is None else timeout_s,
        record=record,
        json_output=json_output,
        preset=preset,
        plugins=plugins,
    )


def _inspect_vehicle(
    *,
    vehicle_id: str | None,
    frames: int,
    interval_s: float,
    timeout_s: float,
    record: bool,
    json_output: bool,
    preset: str | None,
    plugins: list[str] | None,
) -> CommandResult:
    discovery = discover_active_vehicles(
        timeout_s=timeout_s,
        include_picar=True,
        include_chase_sim=True,
        include_inactive=True,
    )
    vehicle, selection, error = _select_vehicle(discovery, vehicle_id)
    if error or vehicle is None:
        return CommandResult(
            2,
            "\n\n".join(
                [
                    error or "No active vehicle is available.",
                    format_active_vehicles(discovery, include_inactive=True),
                    "Prepare a simulator with: ./cli/automa simulators ensure",
                ]
            ),
        )

    try:
        access = create_vehicle_access(vehicle, timeout_s=timeout_s)
        prepared_runtime = ensure_local_perception_runtime(
            vehicle=vehicle, preset=preset, plugins=plugins
        )
        manifest = prepared_runtime["manifest"]
        activation = step_activation_from_payload(manifest, step="perception")
        runner = load_staged_runner(activation)
        perception_record = {
            **_perception_record(activation),
            "source_tree_sha256": prepared_runtime["source"]["tree_sha256"],
            "bundle_refreshed": prepared_runtime["refreshed"],
        }
    except Exception as exc:
        return CommandResult(2, f"Could not prepare perception runtime: {type(exc).__name__}: {exc}")

    frame_count = max(1, int(frames))
    run_id = _run_id("inspect", str(vehicle.get("vehicle_id") or "vehicle"))
    record_dir, error = _create_record_dir(run_id) if record else (None, None)
    if error is not None:
        return error
    frame_id: str | None = None

    try:
        with _workspace_context(record_dir) as workspace_value:
            working_dir = Path(workspace_value)
            frames_dir = working_dir / "frames"
            results_dir = working_dir / "results"
            frames_dir.mkdir(parents=True, exist_ok=True)
            if record:
                results_dir.mkdir(parents=True, exist_ok=True)

            shared_memory: dict[str, Any] = {}
            runner.reset()
            frame_records: list[dict[str, Any]] = []
            for index in range(frame_count):
                frame_id = f"frame_{index:06d}"
                started = time.perf_counter()
                sensor_frame = access.car.read_sensors(
                    SensorReadRequest(
                        output_dir=frames_dir,
                        read_id=frame_id,
                        requested_sensors=(FRONT_CAMERA_SENSOR_ID,),
                        front_camera_endpoint=access.front_camera_endpoint,
                        image_extension=access.image_extension,
                    )
                )
                reading = sensor_frame.readings.get(FRONT_CAMERA_SENSOR_ID)
                record_item, _ = perceive_sensor_frame(
                    runner,
                    sensor_frame,
                    frame_id=frame_id,
                    frame_index=index,
                    image_path=reading.path if reading is not None else None,
                    shared_memory=shared_memory,
                    metadata={
                        "run_id": run_id,
                        "frame_index": index,
                        "vehicle_id": vehicle.get("vehicle_id"),
                        "recording": record,
                    },
                    result_dir=results_dir / frame_id if record else None,
                    started=started,
                    absence_reason="front camera missing"
                    if reading is None or not reading.path
                    else None,
                )
                frame_records.append(record_item)
                if index + 1 < frame_count and interval_s > 0:
                    time.sleep(max(0.0, float(interval_s)))

            report = _inspect_report(
                run_id=run_id,
                source={
                    "kind": "vehicle",
                    "vehicle_id": vehicle.get("vehicle_id"),
                    "provider": vehicle.get("provider"),
                    "selection": selection,
                },
                perception=perception_record,
                frames=frame_records,
                run_dir=record_dir,
            )
            if record_dir is not None:
                _write_report(record_dir, report)
    except Exception as exc:  # Plugins are third-party code; name the frame that broke.
        return _inspect_failed(frame_id, exc)

    exit_code = 0 if report["summary"]["failed_frames"] == 0 else 1
    if json_output:
        return CommandResult(exit_code, json.dumps(report, indent=2, sort_keys=True))
    return CommandResult(exit_code, _format_inspect_report(report))


def _inspect_images(
    source: Path,
    *,
    record: bool,
    json_output: bool,
    preset: str | None,
    plugins: list[str] | None,
    max_frames: int,
) -> CommandResult:
    source = source.expanduser()
    try:
        image_source = (
            normalize_image_file(source)
            if source.is_file()
            else normalize_image_directory(source, max_frames=max_frames)
        )
        _manifest_path, source_manifest = (
            read_image_manifest(image_source.source_path)
            if source.is_dir()
            else (None, None)
        )
    except SourceValidationError as exc:
        return _unreadable_source(str(exc))
    source = image_source.source_path
    source_manifest = source_manifest or {}
    source_name = source.stem if source.is_file() else source.name

    try:
        if plugins is not None or preset is not None:
            activation = selection_activation(
                "perception", preset=preset, plugins=plugins
            )
        else:
            activation = recorded_selection(
                "perception", source_manifest
            ) or selection_activation("perception")
        runner = PerceptionRunner.from_activation(activation)
    except Exception as exc:  # Plugin construction is a CLI preflight boundary.
        return CommandResult(
            2,
            f"Could not load plugins for perception inspect: {type(exc).__name__}: {exc}",
        )

    run_id = _run_id("inspect", source_name)
    record_dir, error = _create_record_dir(run_id) if record else (None, None)
    if error is not None:
        return error
    frame_id: str | None = None

    try:
        with _workspace_context(record_dir) as workspace_value:
            working_dir = Path(workspace_value)
            results_dir = working_dir / "results"
            if record:
                results_dir.mkdir(parents=True, exist_ok=True)
            shared_memory: dict[str, Any] = {}
            runner.reset()
            frame_records: list[dict[str, Any]] = []
            for frame in image_source.frames:
                frame_id = frame.frame_id
                frame_index = frame.frame_index
                captured_at_ms = frame.timestamp_ms
                image_path = (
                    str(frame.image_path) if frame.image_path is not None else None
                )
                sensor_frame = SensorFrame(
                    read_id=frame_id,
                    readings={
                        FRONT_CAMERA_SENSOR_ID: SensorReading(
                            sensor_id=FRONT_CAMERA_SENSOR_ID,
                            sensor_kind="camera",
                            captured_at_ms=captured_at_ms,
                            path=image_path,
                            metadata={"source": "images"},
                        )
                    }
                    if not frame.absent
                    else {},
                    started_at_ms=captured_at_ms,
                    completed_at_ms=captured_at_ms,
                    metadata={"source": "images", "source_path": str(source)},
                )
                item, _ = perceive_sensor_frame(
                    runner,
                    sensor_frame,
                    frame_id=frame_id,
                    frame_index=frame_index,
                    image_path=image_path,
                    shared_memory=shared_memory,
                    metadata={"run_id": run_id, "frame_index": frame_index},
                    result_dir=(results_dir / f"frame_{frame.position:06d}")
                    if record
                    else None,
                    absence_reason=frame.absence_reason if frame.absent else None,
                )
                frame_records.append(item)

            report = _inspect_report(
                run_id=run_id,
                source={"kind": "images", "path": str(source)},
                perception=_perception_record(activation),
                frames=frame_records,
                run_dir=record_dir,
            )
            if record_dir is not None:
                _write_report(record_dir, report)
    except Exception as exc:  # Plugins are third-party code; name the frame that broke.
        return _inspect_failed(frame_id, exc)

    exit_code = 0 if report["summary"]["failed_frames"] == 0 else 1
    if json_output:
        return CommandResult(exit_code, json.dumps(report, indent=2, sort_keys=True))
    return CommandResult(exit_code, _format_inspect_report(report))


def _perception_record(activation: Any) -> dict[str, Any]:
    """The report's ``perception`` entry, the same shape memory inspect records."""

    return {**selection_record(activation), "plugins": list(activation.plugins)}


def _create_record_dir(run_id: str) -> tuple[Path | None, CommandResult | None]:
    record_dir = INSPECT_ROOT / run_id
    try:
        record_dir.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        return None, CommandResult(2, f"Could not record perception inspect run {record_dir}: {exc}")
    return record_dir, None


def _unreadable_source(reason: str) -> CommandResult:
    return CommandResult(2, f"Could not read perception inspect source: {reason}")


def _inspect_failed(frame_id: str | None, exc: Exception) -> CommandResult:
    where = f" at {frame_id}" if frame_id is not None else ""
    return CommandResult(2, f"Perception inspect failed{where}: {type(exc).__name__}: {exc}")


def _select_vehicle(
    discovery: dict[str, Any],
    requested_id: str | None,
) -> tuple[dict[str, Any] | None, str | None, str | None]:
    if requested_id:
        vehicle, error = find_vehicle_by_id(discovery, requested_id)
        return vehicle, "explicit id", error

    vehicles = [item for item in discovery.get("vehicles", []) if isinstance(item, dict)]
    if not vehicles:
        return None, None, "No active vehicles were discovered."
    ranked = sorted(
        vehicles,
        key=lambda item: (
            0 if item.get("provider") == "chase-sim" else 1,
            str(item.get("vehicle_id") or ""),
        ),
    )
    selected = ranked[0]
    if len(ranked) == 1:
        reason = "only active vehicle"
    else:
        reason = "simulator preferred for observation-only experiments"
    return selected, reason, None


def run_perception(
    runner: Any,
    sensor_frame: SensorFrame,
    *,
    shared_memory: dict[str, Any],
    metadata: dict[str, Any],
    output_dir: Path | None = None,
):
    """Run perception on one sensor frame; the step every consumer shares."""

    return runner.perceive(
        build_perception_request(
            sensor_frame,
            shared_memory=shared_memory,
            output_dir=output_dir,
            metadata=metadata,
        )
    )


def perceive_sensor_frame(
    runner: Any,
    sensor_frame: SensorFrame,
    *,
    frame_id: str,
    frame_index: int,
    image_path: str | None,
    shared_memory: dict[str, Any],
    metadata: dict[str, Any],
    result_dir: Path | None = None,
    started: float | None = None,
    absence_reason: str | None = None,
) -> tuple[dict[str, Any], Any]:
    """Perceive one sensor frame and return its frame record and the perception.

    With ``result_dir`` the plugins write their outputs there and the frame's
    ``perception.json`` and ``perception.txt`` are saved beside them. ``started``
    is a ``time.perf_counter()`` reading that begins ``duration_ms`` earlier
    than this call, for callers that time the sensor read too.
    ``absence_reason`` records a gap and resets temporal state without invoking
    plugins, matching the memory and workbench replay boundary.
    """

    if started is None:
        started = time.perf_counter()
    if absence_reason is not None:
        # Match replay's gap boundary: tracks cannot bridge an absent frame.
        runner.reset(shared_memory)
        perception = PerceptionText(
            schema=PERCEPTION_TEXT_SCHEMA,
            plugin_id=runner.plugin_id,
            status="unavailable",
            lines=(f"Perception unavailable: {absence_reason}",),
            signals=(),
            things=(),
        )
    else:
        perception = run_perception(
            runner,
            sensor_frame,
            shared_memory=shared_memory,
            metadata=metadata,
            output_dir=result_dir,
        )
    duration_ms = round((time.perf_counter() - started) * 1000.0, 3)
    record = _frame_record(
        frame_id=frame_id,
        frame_index=frame_index,
        image_path=image_path,
        sensor_frame=sensor_frame,
        perception=perception,
        duration_ms=duration_ms,
        runtime_metrics=_runtime_metrics(),
    )
    if absence_reason is not None:
        record["absence_reason"] = absence_reason
    if result_dir is not None:
        result_dir.mkdir(parents=True, exist_ok=True)
        (result_dir / "perception.json").write_text(
            json.dumps(record, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        (result_dir / "perception.txt").write_text(
            perception.text + "\n", encoding="utf-8"
        )
    return record, perception


def _frame_record(
    *,
    frame_id: str,
    frame_index: int,
    image_path: str | None,
    sensor_frame: SensorFrame,
    perception,
    duration_ms: float,
    runtime_metrics: dict[str, Any],
) -> dict[str, Any]:
    return {
        "frame_id": frame_id,
        "frame_index": frame_index,
        "image_path": image_path,
        "captured_at_ms": sensor_frame.completed_at_ms,
        "duration_ms": duration_ms,
        "status": perception.status,
        "signal_count": len(perception.signals),
        "thing_count": len(perception.things),
        "thing_kinds": dict(Counter(thing.kind for thing in perception.things)),
        "plugin_runs": [run.to_dict() for run in perception.plugin_runs],
        "runtime": runtime_metrics,
        "perception": perception.to_dict(),
    }


def _inspect_report(
    *,
    run_id: str,
    source: dict[str, Any],
    perception: dict[str, Any],
    frames: list[dict[str, Any]],
    run_dir: Path | None,
) -> dict[str, Any]:
    durations = [float(frame["duration_ms"]) for frame in frames]
    steady_durations = durations[1:] if len(durations) > 1 else durations
    rss_values = [
        float(frame.get("runtime", {}).get("peak_rss_mb"))
        for frame in frames
        if frame.get("runtime", {}).get("peak_rss_mb") is not None
    ]
    statuses = Counter(str(frame["status"]) for frame in frames)
    thing_kinds: Counter[str] = Counter()
    plugin_durations: dict[str, list[float]] = defaultdict(list)
    plugin_statuses: dict[str, Counter[str]] = defaultdict(Counter)
    for frame in frames:
        thing_kinds.update(frame["thing_kinds"])
        for run in frame["plugin_runs"]:
            plugin_id = str(run["plugin_id"])
            plugin_durations[plugin_id].append(float(run["duration_ms"]))
            plugin_statuses[plugin_id][str(run["status"])] += 1

    failed_frames = sum(statuses[status] for status in ("partial", "error", "unavailable"))
    summary = {
        "frames": len(frames),
        "failed_frames": failed_frames,
        "status_counts": dict(statuses),
        "thing_kinds": dict(thing_kinds),
        "latency_ms": {
            "cold_start": round(durations[0], 3) if durations else 0.0,
            "median": round(statistics.median(durations), 3) if durations else 0.0,
            "p95": round(_percentile(durations, 0.95), 3) if durations else 0.0,
            "max": round(max(durations), 3) if durations else 0.0,
            "steady_median": round(statistics.median(steady_durations), 3) if steady_durations else 0.0,
            "steady_p95": round(_percentile(steady_durations, 0.95), 3) if steady_durations else 0.0,
        },
        "memory_mb": {
            "peak_rss": round(max(rss_values), 3) if rss_values else 0.0,
        },
        "plugins": {
            plugin_id: {
                "status_counts": dict(plugin_statuses[plugin_id]),
                "median_ms": round(statistics.median(values), 3),
                "p95_ms": round(_percentile(values, 0.95), 3),
            }
            for plugin_id, values in sorted(plugin_durations.items())
        },
    }
    summary["representation_health"] = evaluate_perception_frames(frames)
    return {
        "schema": PERCEPTION_INSPECT_SCHEMA,
        "run_id": run_id,
        "created_at_ms": int(time.time() * 1000),
        "source": source,
        "perception": perception,
        "run_dir": display_path(run_dir) if run_dir is not None else None,
        "summary": summary,
        "frames": frames,
    }


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * fraction)))
    return float(ordered[index])


def _write_report(run_dir: Path, report: dict[str, Any]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (run_dir / "summary.txt").write_text(_format_inspect_report(report) + "\n", encoding="utf-8")


def _format_inspect_report(report: dict[str, Any]) -> str:
    summary = report["summary"]
    source = report["source"]
    perception = report["perception"]
    source_label = source.get("vehicle_id") or source.get("path") or source.get("kind")
    detail = f"{summary['frames']} frames"
    if source.get("selection"):
        detail += f"; {source['selection']}"
    lines = [
        "Perception inspect",
        "------------------",
        f"Source: {source_label} ({detail})",
        f"Perception: {perception['preset']} ({', '.join(perception['plugins'])})",
        "",
        f"Failed frames: {summary['failed_frames']}",
        f"Statuses: {_format_counts(summary['status_counts'])}",
        f"Evidence: {_format_counts(summary['thing_kinds'])}",
        f"Latency: cold {summary['latency_ms']['cold_start']:.3f} ms; "
        f"steady median {summary['latency_ms']['steady_median']:.3f} ms, "
        f"p95 {summary['latency_ms']['steady_p95']:.3f} ms",
        f"Peak memory: {summary['memory_mb']['peak_rss']:.3f} MiB",
        f"Representation health: {summary['representation_health']['score']:.3f} "
        "(availability, geometry, count stability and frame-to-frame continuity)",
        "",
        "Plugins",
    ]
    for plugin_id, plugin in summary["plugins"].items():
        lines.append(
            f"  {plugin_id}: {_format_counts(plugin['status_counts'])}; "
            f"median {plugin['median_ms']:.3f} ms"
        )
    if report["run_dir"]:
        lines.extend(["", f"Recorded: {report['run_dir']}"])
    return "\n".join(lines)


def _format_counts(counts: dict[str, Any]) -> str:
    if not counts:
        return "none"
    return ", ".join(f"{key}={counts[key]}" for key in sorted(counts))



def _workspace_context(record_dir: Path | None) -> AbstractContextManager[str | Path]:
    if record_dir is not None:
        return nullcontext(record_dir)
    return tempfile.TemporaryDirectory(prefix="automa_perception_inspect_")


def _run_id(kind: str, source: str) -> str:
    """Collision-resistant run identity (same-second and concurrent-safe)."""

    import uuid

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    # Microseconds + short uuid so sequential runs in the same second never collide.
    stamp = f"{timestamp}-{int(time.time() * 1_000_000) % 1_000_000:06d}-{uuid.uuid4().hex[:8]}"
    return f"{kind}-{safe_path_part(source)}-{stamp}"


def _runtime_metrics() -> dict[str, Any]:
    peak = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    divisor = 1024.0 * 1024.0 if sys.platform == "darwin" else 1024.0
    return {"peak_rss_mb": round(peak / divisor, 3)}
