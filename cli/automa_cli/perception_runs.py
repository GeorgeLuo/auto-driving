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
from typing import Any, TextIO

from autonomy.perception import build_perception_request
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorReadRequest, SensorReading, SensorSnapshot

from .lab_plugins import LabPerceptionMapper, candidate_status, discover_candidates
from .paths import ROOT, display_path, safe_path_part
from .perception_evaluation import evaluate_perception_frames, write_review_html
from implementations.perception.catalog import (
    DEFAULT_PERCEPTION_ALGORITHM,
    PERCEPTION_ALGORITHMS,
    PERCEPTION_MAPPER_SPEC,
)

from .perception import _load_mapper, ensure_local_perception_runtime
from .vehicle_access import create_vehicle_access
from .vehicles import discover_active_vehicles, find_vehicle_by_id, format_active_vehicles_snapshot


DEFAULT_FRAME_COUNT = 5
DEFAULT_INTERVAL_S = 0.25
_PERCEPTION_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
APPLY_ROOT = Path(os.environ.get("AUTOMA_PERCEPTION_APPLY_ROOT", ROOT / "runtime" / "perception-applies"))


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    message: str


def run_perception_experiment(
    *,
    vehicle_id: str | None = None,
    frames: int = DEFAULT_FRAME_COUNT,
    interval_s: float = DEFAULT_INTERVAL_S,
    timeout_s: float = 3.0,
    record: bool = False,
    json_output: bool = False,
    candidate_id: str | None = None,
    candidate_config: dict[str, Any] | None = None,
    algorithm: str | None = None,
) -> CommandResult:
    if candidate_id is not None and algorithm is not None:
        return CommandResult(2, "Choose either --candidate or --algorithm, not both.")
    if algorithm is not None and algorithm not in PERCEPTION_ALGORITHMS:
        return CommandResult(2, f"Unknown perception algorithm {algorithm!r}.")
    if candidate_config and candidate_id is None:
        return CommandResult(2, "Candidate parameter overrides require --candidate.")
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
                    format_active_vehicles_snapshot(discovery, include_inactive=True),
                    "Prepare a simulator with: ./cli/automa simulators ensure",
                ]
            ),
        )

    try:
        access = create_vehicle_access(vehicle, timeout_s=timeout_s)
        if candidate_id is not None:
            mapper = LabPerceptionMapper(candidate_id, config_overrides=candidate_config)
            mapper_record = mapper.report_descriptor()
            record_root = mapper.candidate.runs_dir
        else:
            prepared_runtime = ensure_local_perception_runtime(vehicle=vehicle, algorithm=algorithm)
            manifest = prepared_runtime["manifest"]
            mapper_spec = manifest["perception"]["mapper_spec"]
            mapper_config = dict(manifest["perception"].get("mapper_config") or {})
            mapper = _load_mapper(
                mapper_spec,
                mapper_config,
                bundle_root=Path(prepared_runtime["bundle"]["root_dir"]),
            )
            mapper_record = {
                "algorithm": manifest["perception"].get("algorithm"),
                "spec": mapper_spec,
                "config": mapper_config,
                "source_tree_sha256": prepared_runtime["source"]["tree_sha256"],
                "bundle_refreshed": prepared_runtime["refreshed"],
            }
            record_root = Path(prepared_runtime["bundle"]["runtime_dir"]) / "perception-runs"
    except Exception as exc:
        return CommandResult(2, f"Could not prepare perception runtime: {type(exc).__name__}: {exc}")

    frame_count = max(1, int(frames))
    run_id = _run_id("perception", str(vehicle.get("vehicle_id") or "vehicle"))
    record_dir = record_root / run_id if record else None
    workspace = _workspace_context(record_dir, prefix="automa_perception_")
    mapper_context: AbstractContextManager[Any] = (
        mapper if isinstance(mapper, LabPerceptionMapper) else nullcontext(mapper)
    )

    try:
        with workspace as workspace_value, mapper_context as active_mapper:
            working_dir = Path(workspace_value)
            frames_dir = working_dir / "frames"
            results_dir = working_dir / "results"
            frames_dir.mkdir(parents=True, exist_ok=True)
            if record:
                results_dir.mkdir(parents=True, exist_ok=True)

            active_mapper.reset()
            frame_records: list[dict[str, Any]] = []
            for index in range(frame_count):
                frame_id = f"frame_{index:06d}"
                started = time.perf_counter()
                snapshot = access.car.read_sensors(
                    SensorReadRequest(
                        output_dir=frames_dir,
                        read_id=frame_id,
                        requested_sensors=(FRONT_CAMERA_SENSOR_ID,),
                        front_camera_endpoint=access.front_camera_endpoint,
                        image_extension=access.image_extension,
                    )
                )
                perception_output_dir = results_dir / frame_id if record else None
                perception = active_mapper.perceive(
                    build_perception_request(
                        snapshot,
                        output_dir=perception_output_dir,
                        metadata={
                            "run_id": run_id,
                            "frame_index": index,
                            "vehicle_id": vehicle.get("vehicle_id"),
                            "recording": record,
                        },
                    )
                )
                duration_ms = round((time.perf_counter() - started) * 1000.0, 3)
                reading = snapshot.readings.get(FRONT_CAMERA_SENSOR_ID)
                record_item = _frame_record(
                    frame_id=frame_id,
                    frame_index=index,
                    image_path=reading.path if reading is not None else None,
                    snapshot=snapshot,
                    perception=perception,
                    duration_ms=duration_ms,
                    runtime_metrics=_runtime_metrics(active_mapper),
                )
                frame_records.append(record_item)
                if record:
                    frame_result_dir = results_dir / frame_id
                    frame_result_dir.mkdir(parents=True, exist_ok=True)
                    (frame_result_dir / "perception.json").write_text(
                        json.dumps(record_item, indent=2, sort_keys=True),
                        encoding="utf-8",
                    )
                    (frame_result_dir / "perception.txt").write_text(perception.text + "\n", encoding="utf-8")
                if index + 1 < frame_count and interval_s > 0:
                    time.sleep(max(0.0, float(interval_s)))

            report = _experiment_report(
                run_id=run_id,
                source={
                    "kind": "vehicle",
                    "vehicle_id": vehicle.get("vehicle_id"),
                    "provider": vehicle.get("provider"),
                    "selection": selection,
                },
                mapper=mapper_record,
                frames=frame_records,
                recording=record,
                run_dir=record_dir,
            )
            if record and record_dir is not None:
                _write_report(record_dir, report)
    except Exception as exc:
        return CommandResult(2, f"Perception capture failed: {type(exc).__name__}: {exc}")

    exit_code = 0 if report["summary"]["failed_frames"] == 0 else 1
    if json_output:
        return CommandResult(exit_code, json.dumps(report, indent=2, sort_keys=True))
    return CommandResult(exit_code, _format_report(report))


def apply_perception_experiment(
    source: Path,
    *,
    record: bool = False,
    json_output: bool = False,
    candidate_id: str | None = None,
    candidate_config: dict[str, Any] | None = None,
    algorithm: str | None = None,
) -> CommandResult:
    if candidate_id is not None and algorithm is not None:
        return CommandResult(2, "Choose either --candidate or --algorithm, not both.")
    if algorithm is not None and algorithm not in PERCEPTION_ALGORITHMS:
        return CommandResult(2, f"Unknown perception algorithm {algorithm!r}.")
    if candidate_config and candidate_id is None:
        return CommandResult(2, "Candidate parameter overrides require --candidate.")
    source = source.expanduser().resolve()
    if not source.exists():
        return CommandResult(2, f"Apply source does not exist: {source}")
    if source.is_file():
        if source.suffix.lower() not in _PERCEPTION_IMAGE_EXTENSIONS:
            return CommandResult(2, f"Apply source is not a supported image: {source}")
        source_dir = source.parent
        source_manifest: dict[str, Any] = {}
        image_paths = [source]
        source_name = source.stem
    elif source.is_dir():
        source_dir = source
        source_manifest = _read_json(source_dir / "run.json")
        if not source_manifest:
            source_manifest = _read_json(source_dir / "report.json")
        image_paths = _source_image_paths(source_dir, source_manifest)
        source_name = source_dir.name
    else:
        return CommandResult(2, f"Apply source is not a file or directory: {source}")
    if not image_paths:
        return CommandResult(2, f"No applicable images found under {source}")

    try:
        if candidate_id is not None:
            mapper = LabPerceptionMapper(candidate_id, config_overrides=candidate_config)
            report_mapper = mapper.report_descriptor()
            record_root = mapper.candidate.runs_dir
        else:
            recorded_mapper = source_manifest.get("mapper") if isinstance(source_manifest, dict) else None
            if algorithm is not None:
                algorithm_config = PERCEPTION_ALGORITHMS[algorithm]
                mapper_spec = str(algorithm_config["mapper_spec"])
                mapper_config = dict(algorithm_config["mapper_config"])
            elif isinstance(recorded_mapper, dict) and not str(
                recorded_mapper.get("algorithm") or ""
            ).startswith("candidate:"):
                mapper_spec = str(recorded_mapper.get("spec") or PERCEPTION_MAPPER_SPEC)
                mapper_config = dict(recorded_mapper.get("config") or {})
                algorithm = recorded_mapper.get("algorithm") or "recorded"
            else:
                algorithm_config = PERCEPTION_ALGORITHMS[DEFAULT_PERCEPTION_ALGORITHM]
                mapper_spec = str(algorithm_config["mapper_spec"])
                mapper_config = dict(algorithm_config["mapper_config"])
                algorithm = DEFAULT_PERCEPTION_ALGORITHM
            mapper = _load_mapper(mapper_spec, mapper_config)
            report_mapper = {"algorithm": algorithm, "spec": mapper_spec, "config": mapper_config}
            record_root = APPLY_ROOT
    except Exception as exc:
        return CommandResult(2, f"Could not load perception mapper for apply: {type(exc).__name__}: {exc}")

    run_id = _run_id("apply", source_name)
    record_dir = record_root / run_id if record else None
    workspace = _workspace_context(record_dir, prefix="automa_apply_")
    mapper_context: AbstractContextManager[Any] = (
        mapper if isinstance(mapper, LabPerceptionMapper) else nullcontext(mapper)
    )

    try:
        with workspace as workspace_value, mapper_context as active_mapper:
            working_dir = Path(workspace_value)
            results_dir = working_dir / "results"
            if record:
                results_dir.mkdir(parents=True, exist_ok=True)
            active_mapper.reset()
            frame_records: list[dict[str, Any]] = []
            for index, image_path in enumerate(image_paths):
                frame_id = f"frame_{index:06d}"
                captured_at_ms = int(image_path.stat().st_mtime * 1000)
                snapshot = SensorSnapshot(
                    read_id=frame_id,
                    readings={
                        FRONT_CAMERA_SENSOR_ID: SensorReading(
                            sensor_id=FRONT_CAMERA_SENSOR_ID,
                            sensor_kind="camera",
                            captured_at_ms=captured_at_ms,
                            path=str(image_path),
                            metadata={"source": "apply"},
                        )
                    },
                    started_at_ms=captured_at_ms,
                    completed_at_ms=captured_at_ms,
                    metadata={"source": "apply", "source_path": str(source)},
                )
                started = time.perf_counter()
                perception = active_mapper.perceive(
                    build_perception_request(
                        snapshot,
                        output_dir=(results_dir / frame_id) if record else None,
                        metadata={"run_id": run_id, "frame_index": index, "apply": True},
                    )
                )
                duration_ms = round((time.perf_counter() - started) * 1000.0, 3)
                item = _frame_record(
                    frame_id=frame_id,
                    frame_index=index,
                    image_path=str(image_path),
                    snapshot=snapshot,
                    perception=perception,
                    duration_ms=duration_ms,
                    runtime_metrics=_runtime_metrics(active_mapper),
                )
                frame_records.append(item)
                if record:
                    frame_result_dir = results_dir / frame_id
                    frame_result_dir.mkdir(parents=True, exist_ok=True)
                    (frame_result_dir / "perception.json").write_text(
                        json.dumps(item, indent=2, sort_keys=True),
                        encoding="utf-8",
                    )
                    (frame_result_dir / "perception.txt").write_text(perception.text + "\n", encoding="utf-8")

            report = _experiment_report(
                run_id=run_id,
                source={"kind": "apply", "path": str(source)},
                mapper=report_mapper,
                frames=frame_records,
                recording=record,
                run_dir=record_dir,
            )
            if record and record_dir is not None:
                _write_report(record_dir, report)
    except Exception as exc:
        return CommandResult(2, f"Applying perception failed: {type(exc).__name__}: {exc}")

    exit_code = 0 if report["summary"]["failed_frames"] == 0 else 1
    if json_output:
        return CommandResult(exit_code, json.dumps(report, indent=2, sort_keys=True))
    return CommandResult(exit_code, _format_report(report))


def compare_perception_candidates(
    source_dir: Path,
    *,
    record: bool = False,
    json_output: bool = False,
    output: TextIO | None = None,
) -> CommandResult:
    source_dir = source_dir.expanduser().resolve()
    if not source_dir.is_dir():
        return CommandResult(2, f"Comparison source is not a directory: {source_dir}")
    ready = [
        candidate
        for candidate in discover_candidates()
        if candidate_status(candidate)["ready"]
    ]
    if not ready:
        return CommandResult(
            2,
            "No ready perception candidates. Run `./cli/automa vehicles perception candidates` for setup guidance.",
        )

    results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for index, candidate in enumerate(ready, start=1):
        if output is not None:
            print(
                f"Comparing {candidate.candidate_id} ({index}/{len(ready)})...",
                file=output,
                flush=True,
            )
        result = apply_perception_experiment(
            source_dir,
            record=record,
            json_output=True,
            candidate_id=candidate.candidate_id,
        )
        if result.exit_code != 0:
            failures.append({"candidate": candidate.candidate_id, "error": result.message})
            continue
        report = json.loads(result.message)
        summary = report["summary"]
        health = summary["representation_health"]
        results.append({
            "candidate": candidate.candidate_id,
            "health_score": health["score"],
            "continuity_match_fraction": health["continuity"]["mean_match_fraction"],
            "continuity_iou": health["continuity"]["mean_matched_iou"],
            "steady_median_ms": summary["latency_ms"]["steady_median"],
            "steady_p95_ms": summary["latency_ms"]["steady_p95"],
            "cold_start_ms": summary["latency_ms"]["cold_start"],
            "peak_rss_mb": summary["memory_mb"]["peak_rss"],
            "thing_kinds": summary["thing_kinds"],
            "failed_frames": summary["failed_frames"],
            "run_dir": report.get("run_dir"),
            "review": report.get("review"),
        })

    payload = {
        "schema": "perception_candidate_comparison_v0",
        "source": str(source_dir),
        "recording": record,
        "interpretation": (
            "Representation health measures contract validity and temporal stability, not semantic accuracy. "
            "Latency and memory are directly comparable only on the same host and run conditions."
        ),
        "results": results,
        "failures": failures,
    }
    exit_code = 0 if results and not failures else 1 if results else 2
    if json_output:
        return CommandResult(exit_code, json.dumps(payload, indent=2, sort_keys=True))
    return CommandResult(exit_code, _format_comparison(payload))


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


def _frame_record(
    *,
    frame_id: str,
    frame_index: int,
    image_path: str | None,
    snapshot: SensorSnapshot,
    perception,
    duration_ms: float,
    runtime_metrics: dict[str, Any],
) -> dict[str, Any]:
    return {
        "frame_id": frame_id,
        "frame_index": frame_index,
        "image_path": image_path,
        "captured_at_ms": snapshot.completed_at_ms,
        "duration_ms": duration_ms,
        "status": perception.status,
        "signal_count": len(perception.signals),
        "thing_count": len(perception.things),
        "thing_kinds": dict(Counter(thing.kind for thing in perception.things)),
        "plugin_runs": [run.to_dict() for run in perception.plugin_runs],
        "runtime": runtime_metrics,
        "perception": perception.to_dict(),
    }


def _experiment_report(
    *,
    run_id: str,
    source: dict[str, Any],
    mapper: dict[str, Any],
    frames: list[dict[str, Any]],
    recording: bool,
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
        "schema": "perception_experiment_v0",
        "run_id": run_id,
        "created_at_ms": int(time.time() * 1000),
        "source": source,
        "mapper": mapper,
        "recording": recording,
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
    review_path = write_review_html(run_dir, report)
    report["review"] = display_path(review_path)
    (run_dir / "run.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    (run_dir / "summary.txt").write_text(_format_report(report) + "\n", encoding="utf-8")


def _format_report(report: dict[str, Any]) -> str:
    summary = report["summary"]
    source = report["source"]
    source_label = source.get("vehicle_id") or source.get("path") or source.get("kind")
    lines = [
        "Perception experiment",
        "---------------------",
        f"source: {source_label}",
    ]
    if source.get("selection"):
        lines.append(f"selection: {source['selection']}")
    lines.extend(
        [
            f"algorithm: {report['mapper'].get('algorithm')}",
            f"frames: {summary['frames']}",
            f"failed frames: {summary['failed_frames']}",
            f"statuses: {_format_counts(summary['status_counts'])}",
            f"evidence: {_format_counts(summary['thing_kinds'])}",
            f"latency: cold {summary['latency_ms']['cold_start']:.3f} ms; "
            f"steady median {summary['latency_ms']['steady_median']:.3f} ms, "
            f"p95 {summary['latency_ms']['steady_p95']:.3f} ms",
            f"peak memory: {summary['memory_mb']['peak_rss']:.3f} MiB",
            f"representation health: {summary['representation_health']['score']:.3f} (not semantic accuracy)",
            f"recording: {'on' if report['recording'] else 'off'}",
        ]
    )
    if report.get("run_dir"):
        lines.append(f"run: {report['run_dir']}")
    if report.get("review"):
        lines.append(f"review: {report['review']}")
    lines.append("plugins:")
    for plugin_id, plugin in summary["plugins"].items():
        lines.append(
            f"- {plugin_id}: {_format_counts(plugin['status_counts'])}; "
            f"median {plugin['median_ms']:.3f} ms"
        )
    return "\n".join(lines)


def _format_counts(counts: dict[str, Any]) -> str:
    if not counts:
        return "none"
    return ", ".join(f"{key}={counts[key]}" for key in sorted(counts))


def _format_comparison(payload: dict[str, Any]) -> str:
    lines = [
        "Perception candidate comparison",
        "-------------------------------",
        f"source: {payload['source']}",
        "health is representation stability, not semantic accuracy",
        "",
        f"{'candidate':<20} {'health':>7} {'match':>7} {'IoU':>7} {'steady':>10} {'RSS':>11}",
    ]
    for item in payload["results"]:
        lines.append(
            f"{item['candidate']:<20} {item['health_score']:>7.3f} "
            f"{item['continuity_match_fraction']:>7.3f} {item['continuity_iou']:>7.3f} "
            f"{item['steady_median_ms']:>8.1f}ms {item['peak_rss_mb']:>8.1f}MiB"
        )
        lines.append(f"  evidence: {_format_counts(item['thing_kinds'])}")
        if item.get("review"):
            lines.append(f"  review: {item['review']}")
    for failure in payload["failures"]:
        lines.append(f"- {failure['candidate']} failed: {failure['error']}")
    lines.append(f"recording: {'on' if payload['recording'] else 'off'}")
    return "\n".join(lines)


def _source_image_paths(source_dir: Path, manifest: dict[str, Any]) -> list[Path]:
    frames = manifest.get("frames") if isinstance(manifest, dict) else None
    if isinstance(frames, list):
        paths = []
        for frame in frames:
            image_path = frame.get("image_path") if isinstance(frame, dict) else None
            if not isinstance(image_path, str):
                continue
            resolved_path = _resolve_manifest_image(source_dir, image_path)
            if resolved_path is not None:
                paths.append(resolved_path)
        if paths:
            return paths

    startup_results = manifest.get("results") if isinstance(manifest, dict) else None
    if isinstance(startup_results, list):
        startup_paths: list[Path] = []
        for result in startup_results:
            if not isinstance(result, dict):
                continue
            for key in ("before_capture", "after_capture"):
                capture = result.get(key)
                image_path = capture.get("path") if isinstance(capture, dict) else None
                if not isinstance(image_path, str):
                    continue
                resolved_path = _resolve_manifest_image(source_dir, image_path)
                if resolved_path is not None:
                    startup_paths.append(resolved_path)
        if startup_paths:
            return startup_paths

    search_dir = source_dir / "frames" if (source_dir / "frames").is_dir() else source_dir
    return sorted(
        path
        for path in search_dir.iterdir()
        if path.is_file() and path.suffix.lower() in _PERCEPTION_IMAGE_EXTENSIONS
    )


def _workspace_context(record_dir: Path | None, *, prefix: str) -> AbstractContextManager[str | Path]:
    if record_dir is not None:
        return nullcontext(record_dir)
    return tempfile.TemporaryDirectory(prefix=prefix)


def _resolve_manifest_image(source_dir: Path, value: str) -> Path | None:
    path = Path(value)
    candidates = [
        path if path.is_absolute() else source_dir / path,
        source_dir / "frames" / path.name,
    ]
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.is_file():
            return resolved
    return None


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _run_id(kind: str, source: str) -> str:
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    return f"{kind}-{safe_path_part(source)}-{timestamp}"


def _runtime_metrics(mapper: Any) -> dict[str, Any]:
    candidate_metrics = getattr(mapper, "last_runtime_metrics", None)
    if isinstance(candidate_metrics, dict) and candidate_metrics:
        return dict(candidate_metrics)
    peak = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    divisor = 1024.0 * 1024.0 if sys.platform == "darwin" else 1024.0
    return {"peak_rss_mb": round(peak / divisor, 3)}
