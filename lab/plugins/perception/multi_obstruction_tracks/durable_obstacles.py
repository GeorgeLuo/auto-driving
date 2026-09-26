"""Reproducible P1 replay of the frozen best-bright detector."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from autonomy.decision import DecisionFrameContext, Observation
from autonomy.perception import PerceptionDiagnosticSink, PerceptionPluginInputs, PerceivedThing
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorReading, SensorSnapshot
from lab.plugins.memory.multi_obstruction_tracks.plugin import MultiObstructionMemory
from implementations.perception.components import CameraFrame
from lab.plugins.perception.multi_obstruction_tracks.src.plugin import MultiObstructionTracksPlugin


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _json_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class _NoopDiagnostics(PerceptionDiagnosticSink):
    def __init__(self) -> None:
        super().__init__(output_dir=None, plugin_id="p1", allowed_artifacts=())


def replay_capture(manifest_path: Path, config_path: Path, output_dir: Path) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    frames = list(manifest.get("camera_frames") or [])
    if len(frames) != int(manifest.get("camera_frame_count", len(frames))):
        raise ValueError("manifest camera frame count disagrees with camera_frames")
    config_payload = json.loads(config_path.read_text(encoding="utf-8"))
    config = dict(config_payload["config"])
    historical_max = int(config.get("max_tracks", 0))
    config["max_tracks"] = max(32, historical_max)
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "overlays").mkdir()
    capture_dir = manifest_path.parent
    plugin = MultiObstructionTracksPlugin(**config)
    tracking = MultiObstructionMemory()
    shared_memory: dict[str, Any] = {}  # Run-owned history, independent of plugin lifetime.
    diagnostics = _NoopDiagnostics()
    ledger: list[dict[str, Any]] = []
    detections_path = output_dir / "detections.jsonl"
    failures: list[dict[str, Any]] = []
    with detections_path.open("w", encoding="utf-8") as detections_file:
        for position, item in enumerate(frames):
            rel_image = Path(item["image"])
            image_path = capture_dir / rel_image
            available = image_path.is_file()
            entry = {"position": position, "frame_id": item["frame_id"], "frame_index": item["frame_index"],
                     "timestamp_ms": item["captured_at_ms"], "image": str(image_path),
                     "image_sha256": sha256(image_path) if available else None, "available": available}
            ledger.append(entry)
            if not available:
                tracking.reset(shared_memory)
                failures.append({"frame_id": item["frame_id"], "reason": "missing_image"})
                detections_file.write(json.dumps({**entry, "status": "unavailable", "things": []}, sort_keys=True) + "\n")
                continue
            bgr = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
            if bgr is None:
                tracking.reset(shared_memory)
                failures.append({"frame_id": item["frame_id"], "reason": "decode_failed"})
                detections_file.write(json.dumps({**entry, "status": "error", "things": []}, sort_keys=True) + "\n")
                continue
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            frame = CameraFrame("front_camera", int(item["captured_at_ms"]), np.ascontiguousarray(rgb), image_path, {"source_id": item["frame_id"]})
            batch = plugin.perceive(PerceptionPluginInputs(
                item["frame_id"], int(item["captured_at_ms"]), {"frame": frame},
                diagnostics, {"sequence_index": position}, memory=shared_memory,
            ))
            sensors = SensorSnapshot(
                read_id=item["frame_id"], readings={FRONT_CAMERA_SENSOR_ID: SensorReading(
                    sensor_id=FRONT_CAMERA_SENSOR_ID, sensor_kind="camera",
                    captured_at_ms=frame.captured_at_ms, value=rgb,
                    metadata={"color_space": "RGB"},
                )}, started_at_ms=frame.captured_at_ms, completed_at_ms=frame.captured_at_ms,
            )
            tracking.update(DecisionFrameContext(
                frame_id=item["frame_id"], frame_index=position,
                timestamp_ms=frame.captured_at_ms, sensor_snapshot=sensors, memory=shared_memory,
            ), Observation(
                observation_id=item["frame_id"], created_at_ms=frame.captured_at_ms,
                sensor_snapshot={}, things=tuple(thing.to_dict() for thing in batch.things),
                signals=tuple(signal.to_dict() for signal in batch.signals),
            ))
            tracked = shared_memory["decision.observation"]
            things = []
            for payload in tracked.things:
                thing = PerceivedThing.from_dict(payload)
                loc = thing.location
                props = dict(thing.properties)
                box = list(loc.bbox_xyxy_norm or [])
                if len(box) != 4 or not (0 <= box[0] <= box[2] <= 1 and 0 <= box[1] <= box[3] <= 1):
                    raise ValueError(f"invalid normalized box on {item['frame_id']}: {box}")
                things.append({"thing_id": thing.thing_id, "bbox_xyxy_norm": box, "confidence": thing.confidence, "properties": props})
                x0, y0, x1, y1 = [int(round(v * (bgr.shape[1] if i % 2 == 0 else bgr.shape[0]))) for i, v in enumerate(box)]
                cv2.rectangle(bgr, (x0, y0), (x1, y1), (0, 220, 0), 2)
                cv2.putText(bgr, thing.thing_id, (x0, max(15, y0 - 4)), cv2.FONT_HERSHEY_SIMPLEX, .45, (0, 220, 0), 1, cv2.LINE_AA)
            record = {**entry, "status": "ok", "width_px": int(bgr.shape[1]), "height_px": int(bgr.shape[0]), "things": things,
                      "signal_count": len(tracked.signals), "measurement": {**batch.measurements, "tracking": tracked.metadata["tracking"]}}
            detections_file.write(json.dumps(record, sort_keys=True, default=str) + "\n")
            cv2.imwrite(str(output_dir / "overlays" / f"{position:04d}-{item['frame_id']}.jpg"), bgr)
    frozen = {"schema": "durable_obstacle_p1_baseline_v1", "manifest": str(manifest_path), "manifest_sha256": sha256(manifest_path),
              "config_path": str(config_path), "config_sha256": sha256(config_path), "source_code": "lab/plugins/perception/multi_obstruction_tracks/src/plugin.py",
              "source_code_sha256": sha256(Path("lab/plugins/perception/multi_obstruction_tracks/src/plugin.py")), "config": config,
              "memory_source_code_sha256": sha256(Path("lab/plugins/memory/multi_obstruction_tracks/plugin.py")),
              "tracker_source_code_sha256": sha256(Path("lab/plugins/memory/multi_obstruction_tracks/tracker.py")),
              "config_hash": _json_hash(config), "historical_max_tracks": historical_max, "effective_max_tracks": config["max_tracks"],
              "frame_count": len(frames), "processed_count": len(frames) - len(failures), "failures": failures,
              "frame_ledger_hash": _json_hash(ledger), "reset_temporal_state": True}
    (output_dir / "frames.json").write_text(json.dumps(ledger, indent=2, sort_keys=True), encoding="utf-8")
    (output_dir / "frozen-inputs.json").write_text(json.dumps(frozen, indent=2, sort_keys=True), encoding="utf-8")
    return frozen


DEFAULT_TRACKING_CONFIG: dict[str, Any] = {
    "schema_version": "durable_obstacle_tracking_policy_v1",
    "minimum_independent_observations": {"value": 3, "unit": "unique_frames"},
    "minimum_time_span": {"value": 0.20, "unit": "seconds"},
    "minimum_supported_fraction": {"value": 0.50, "unit": "observed_frames_over_visible_interval"},
    "maximum_gap_duration": {"value": 0.75, "unit": "seconds"},
    "maximum_center_trend_residual": {"value": 0.12, "unit": "normalized_center_l2"},
    "maximum_scale_change_residual": {"value": 0.35, "unit": "log_scale_residual"},
    "minimum_flow_support": {"value": 0.0, "unit": "score_0_to_1"},
    "association_max_center_distance": {"value": 0.20, "unit": "normalized_center_l2"},
    "association_max_scale_log_distance": {"value": 0.80, "unit": "absolute_log_width_height_ratio"},
}


def _thresholds(config: dict[str, Any]) -> dict[str, float]:
    return {key: float(value["value"]) for key, value in config.items() if isinstance(value, dict) and "value" in value}


def _center(box: list[float]) -> tuple[float, float]:
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


def _scale(box: list[float]) -> tuple[float, float]:
    return (max(box[2] - box[0], 1e-9), max(box[3] - box[1], 1e-9))


def _fit_residual(times: list[float], values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    x = np.asarray(times, dtype=float)
    y = np.asarray(values, dtype=float)
    x = x - x[0]
    design = np.column_stack((np.ones(len(x)), x))
    prediction = design @ np.linalg.lstsq(design, y, rcond=None)[0]
    return float(np.sqrt(np.mean((y - prediction) ** 2)))


def _observation(record: dict[str, Any], thing: dict[str, Any], ordinal: int) -> dict[str, Any]:
    box = [float(v) for v in thing["bbox_xyxy_norm"]]
    cx, cy = _center(box)
    width, height = _scale(box)
    props = thing.get("properties") if isinstance(thing.get("properties"), dict) else {}
    return {"observation_id": f"{record['frame_id']}:{ordinal}", "frame_id": record["frame_id"],
            "position": int(record["position"]), "timestamp_ms": int(record["timestamp_ms"]),
            "image_sha256": record.get("image_sha256"), "detection_id": thing.get("thing_id"),
            "bbox_xyxy_norm": box, "center": [cx, cy], "scale": [width, height],
            "confidence": thing.get("confidence"), "shape_support": props.get("shape_support", 0.0),
            "flow_support": props.get("flow_support", 0.0), "source_status": "observed"}


def _association_cost(previous: dict[str, Any], current: dict[str, Any]) -> tuple[float, float, float]:
    dc = float(np.linalg.norm(np.subtract(previous["center"], current["center"])))
    ps = previous["scale"]
    cs = current["scale"]
    ds = float(np.linalg.norm(np.log(np.asarray(cs) / np.asarray(ps))))
    return dc + 0.25 * ds, dc, ds


def local_durability(detections_path: Path, output_dir: Path, tracking_config_path: Path | None = None) -> dict[str, Any]:
    """Build auditable tracks from frozen detector JSONL; never invokes a detector."""
    records = [json.loads(line) for line in detections_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    config = json.loads(tracking_config_path.read_text(encoding="utf-8")) if tracking_config_path else DEFAULT_TRACKING_CONFIG
    thresholds = _thresholds(config)
    output_dir.mkdir(parents=True, exist_ok=False)
    tracks: list[dict[str, Any]] = []
    active: dict[int, dict[str, Any]] = {}
    alternatives: list[dict[str, Any]] = []
    next_id = 0
    per_frame: list[dict[str, Any]] = []
    for record in records:
        observations = [_observation(record, thing, i) for i, thing in enumerate(record.get("things", []))]
        candidates: list[tuple[float, int, int, float, float]] = []
        for tid, track in active.items():
            for oi, obs in enumerate(observations):
                cost, dc, ds = _association_cost(track["observations"][-1], obs)
                gap = (obs["timestamp_ms"] - track["observations"][-1]["timestamp_ms"]) / 1000.0
                if dc <= thresholds["association_max_center_distance"] and ds <= thresholds["association_max_scale_log_distance"] and gap <= thresholds["maximum_gap_duration"]:
                    candidates.append((cost, tid, oi, dc, ds))
        candidates.sort(key=lambda item: (item[0], item[1], item[2]))
        used_tracks: set[int] = set(); used_obs: set[int] = set(); matches = []
        for cost, tid, oi, dc, ds in candidates:
            if tid in used_tracks or oi in used_obs:
                continue
            used_tracks.add(tid); used_obs.add(oi); matches.append((tid, oi, cost, dc, ds))
        for cost, tid, oi, dc, ds in candidates:
            if (tid, oi) not in [(a, b) for a, b, *_ in matches]:
                alternatives.append({"frame_id": record["frame_id"], "track_id": tid, "observation_id": observations[oi]["observation_id"], "cost": cost, "center_distance": dc, "scale_log_distance": ds, "selected": False})
        for tid, oi, cost, dc, ds in matches:
            observations[oi]["association"] = {"status": "matched", "center_distance": dc, "scale_log_distance": ds, "cost": cost}
            active[tid]["observations"].append(observations[oi])
        for oi, obs in enumerate(observations):
            if oi not in used_obs:
                tid = next_id; next_id += 1
                obs["association"] = {"status": "new"}
                active[tid] = {"track_id": tid, "observations": [obs], "last_seen_ms": obs["timestamp_ms"]}
        for tid in list(active):
            if active[tid]["observations"][-1]["timestamp_ms"] != record["timestamp_ms"]:
                gap = (record["timestamp_ms"] - active[tid]["observations"][-1]["timestamp_ms"]) / 1000.0
                if gap > thresholds["maximum_gap_duration"]:
                    active[tid]["closed_reason"] = "gap_exceeded"
                    tracks.append(active.pop(tid))
        per_frame.append({"frame_id": record["frame_id"], "position": record["position"], "observations": observations,
                          "active_track_ids": sorted(active), "association_alternatives": [a for a in alternatives if a["frame_id"] == record["frame_id"]]})
    tracks.extend(active.values())
    decisions = []
    emitted = []
    for track in tracks:
        obs = track["observations"]
        times = [o["timestamp_ms"] / 1000.0 for o in obs]
        unique_images = len({o.get("image_sha256") for o in obs})
        gaps = [round((b - a) / 1000.0, 6) for a, b in zip([o["timestamp_ms"] for o in obs], [o["timestamp_ms"] for o in obs][1:])]
        center_residual = _fit_residual(times, [o["center"][0] for o in obs]) + _fit_residual(times, [o["center"][1] for o in obs])
        scale_residual = _fit_residual(times, [math.log(o["scale"][0] * o["scale"][1]) for o in obs])
        span = max(times) - min(times) if times else 0.0
        visible_interval = span + (max(gaps) if gaps else 0.0)
        supported_fraction = len(obs) / max(1.0, len(obs) + sum(1 for gap in gaps if gap > 0.0))
        flow_values = [float(o.get("flow_support") or 0.0) for o in obs]
        checks = {
            "minimum_independent_observations": {"measured": unique_images, "threshold": thresholds["minimum_independent_observations"], "passed": unique_images >= thresholds["minimum_independent_observations"]},
            "minimum_time_span": {"measured": span, "threshold": thresholds["minimum_time_span"], "passed": span >= thresholds["minimum_time_span"]},
            "minimum_supported_fraction": {"measured": supported_fraction, "threshold": thresholds["minimum_supported_fraction"], "passed": supported_fraction >= thresholds["minimum_supported_fraction"]},
            "maximum_gap_duration": {"measured": max(gaps or [0.0]), "threshold": thresholds["maximum_gap_duration"], "passed": max(gaps or [0.0]) <= thresholds["maximum_gap_duration"]},
            "maximum_center_trend_residual": {"measured": center_residual, "threshold": thresholds["maximum_center_trend_residual"], "passed": center_residual <= thresholds["maximum_center_trend_residual"]},
            "maximum_scale_change_residual": {"measured": scale_residual, "threshold": thresholds["maximum_scale_change_residual"], "passed": scale_residual <= thresholds["maximum_scale_change_residual"]},
            "minimum_flow_support": {"measured": min(flow_values or [0.0]), "threshold": thresholds["minimum_flow_support"], "passed": min(flow_values or [0.0]) >= thresholds["minimum_flow_support"]},
        }
        failed = [name for name, check in checks.items() if not check["passed"]]
        if "minimum_independent_observations" in failed or "minimum_time_span" in failed:
            verdict = "uncertain"
        elif "maximum_gap_duration" in failed or "maximum_center_trend_residual" in failed or "maximum_scale_change_residual" in failed:
            verdict = "rejected"
        else:
            verdict = "durable" if not failed else "uncertain"
        decision = {"track_id": f"track_{track['track_id']:04d}", "verdict": verdict, "decision_source": "local", "observations": obs,
                    "visible_interval": {"start_frame_id": obs[0]["frame_id"], "end_frame_id": obs[-1]["frame_id"], "duration_s": span},
                    "gaps_s": gaps, "metrics": {"independent_observations": unique_images, "supported_fraction": supported_fraction, "center_trend_residual": center_residual, "scale_change_residual": scale_residual, "min_flow_support": min(flow_values or [0.0])},
                    "checks": checks, "failed_checks": failed, "counterevidence": [{"reason": "duplicate_image"}] if unique_images < len(obs) else [], "prediction_count": 0}
        decisions.append(decision)
        for o in obs:
            emitted.append({"frame_id": o["frame_id"], "track_id": decision["track_id"], "source_status": "observed", "bbox_xyxy_norm": o["bbox_xyxy_norm"], "verdict": verdict, "detection_id": o["detection_id"]})
    (output_dir / "tracking-config.json").write_text(json.dumps(config, indent=2, sort_keys=True), encoding="utf-8")
    (output_dir / "tracks.json").write_text(json.dumps({"schema_version": "durable_tracks_v1", "source_detections": str(detections_path), "tracks": decisions, "association_alternatives": alternatives}, indent=2, sort_keys=True), encoding="utf-8")
    (output_dir / "decisions.json").write_text(json.dumps({"schema_version": "durability_decisions_v1", "policy": config, "decisions": decisions}, indent=2, sort_keys=True), encoding="utf-8")
    (output_dir / "emitted-boxes.jsonl").write_text("\n".join(json.dumps(x, sort_keys=True) for x in emitted) + "\n", encoding="utf-8")
    (output_dir / "per-frame.json").write_text(json.dumps(per_frame, indent=2, sort_keys=True), encoding="utf-8")
    (output_dir / "diagnostics.json").write_text(json.dumps({"schema_version": "durability_diagnostics_v1", "trajectories": [{"track_id": d["track_id"], "points": [[o["frame_id"], *o["center"]] for o in d["observations"]], "verdict": d["verdict"]} for d in decisions], "disputed_spans": [a for a in alternatives if not a["selected"]]}, indent=2, sort_keys=True), encoding="utf-8")
    return {"track_count": len(decisions), "verdict_counts": {v: sum(d["verdict"] == v for d in decisions) for v in ("durable", "rejected", "uncertain")}, "frame_count": len(per_frame), "detection_count": len(emitted)}


def _corrected_observation(record: dict[str, Any], thing: dict[str, Any], ordinal: int) -> dict[str, Any]:
    obs = _observation(record, thing, ordinal)
    props = dict(thing.get("properties") or {})
    event = str(props.get("track_event") or props.get("source_status") or "observed").lower()
    status = "predicted" if event == "predicted" else "held" if event == "held" else "observed"
    obs.update({"source_status": status, "track_event": event, "source_properties": props})
    obs["is_actual"] = status == "observed"
    return obs


def _local_window_metrics(observations: list[dict[str, Any]], window: int = 4) -> tuple[float, float, list[str]]:
    """Return max local center/scale residuals and offending observation IDs."""
    if len(observations) < 3:
        return 0.0, 0.0, []
    center_errors: list[float] = []
    scale_errors: list[float] = []
    offending: list[str] = []
    for index in range(len(observations)):
        lo = max(0, index - window + 1)
        segment = observations[lo:index + 1]
        if len(segment) < 3:
            continue
        times = [(item["timestamp_ms"] - segment[0]["timestamp_ms"]) / 1000.0 for item in segment]
        predicted: list[float] = []
        for axis in (0, 1):
            values = [item["center"][axis] for item in segment]
            x = np.asarray(times, dtype=float)
            design = np.column_stack((np.ones(len(x)), x))
            fit = design @ np.linalg.lstsq(design, np.asarray(values), rcond=None)[0]
            predicted.append(float(fit[-1]))
        center_error = float(np.linalg.norm(np.subtract(segment[-1]["center"], predicted)))
        scale_errors_axes: list[float] = []
        for axis in (0, 1):
            values = [math.log(item["scale"][axis]) for item in segment]
            x = np.asarray(times, dtype=float)
            design = np.column_stack((np.ones(len(x)), x))
            fit = design @ np.linalg.lstsq(design, np.asarray(values), rcond=None)[0]
            scale_errors_axes.append(abs(math.log(segment[-1]["scale"][axis]) - float(fit[-1])))
        scale_error = float(np.linalg.norm(scale_errors_axes))
        center_errors.append(center_error); scale_errors.append(scale_error)
        if center_error > 0.12 or scale_error > 0.35:
            offending.append(segment[-1]["observation_id"])
    return max(center_errors or [0.0]), max(scale_errors or [0.0]), offending


def _local_window_evidence(observations: list[dict[str, Any]], center_threshold: float, scale_threshold: float, window: int = 4) -> dict[str, Any]:
    windows: list[dict[str, Any]] = []
    for index in range(len(observations)):
        lo = max(0, index - window + 1)
        segment = observations[lo:index + 1]
        if len(segment) < 3:
            continue
        times = [(o["timestamp_ms"] - segment[0]["timestamp_ms"]) / 1000.0 for o in segment]
        x = np.asarray(times, dtype=float)
        design = np.column_stack((np.ones(len(x)), x))
        fitted_center: list[float] = []
        center_residual_axes: list[float] = []
        fitted_scale: list[float] = []
        scale_residual_axes: list[float] = []
        for axis in (0, 1):
            values = np.asarray([o["center"][axis] for o in segment], dtype=float)
            fit = design @ np.linalg.lstsq(design, values, rcond=None)[0]
            fitted_center.append(float(fit[-1])); center_residual_axes.append(float(values[-1] - fit[-1]))
            scale_values = np.asarray([math.log(o["scale"][axis]) for o in segment], dtype=float)
            scale_fit = design @ np.linalg.lstsq(design, scale_values, rcond=None)[0]
            fitted_scale.append(float(scale_fit[-1])); scale_residual_axes.append(float(scale_values[-1] - scale_fit[-1]))
        center_residual = float(np.linalg.norm(center_residual_axes)); scale_residual = float(np.linalg.norm(scale_residual_axes))
        windows.append({"start_frame_id": segment[0]["frame_id"], "end_frame_id": segment[-1]["frame_id"], "start_timestamp_ms": segment[0]["timestamp_ms"], "end_timestamp_ms": segment[-1]["timestamp_ms"], "observation_ids": [o["observation_id"] for o in segment], "center_measurements": [o["center"] for o in segment], "scale_measurements": [o["scale"] for o in segment], "fitted_last_center": fitted_center, "fitted_last_log_scale": fitted_scale, "center_residual_l2": center_residual, "scale_residual_l2": scale_residual, "thresholds": {"center_residual_l2": center_threshold, "scale_residual_l2": scale_threshold}, "passed": center_residual <= center_threshold and scale_residual <= scale_threshold, "offending_observation_ids": [segment[-1]["observation_id"]] if center_residual > center_threshold or scale_residual > scale_threshold else []})
    return {"window_size": window, "windows": windows, "max_center_residual_l2": max([w["center_residual_l2"] for w in windows] or [0.0]), "max_scale_residual_l2": max([w["scale_residual_l2"] for w in windows] or [0.0]), "offending_observation_ids": [i for w in windows for i in w["offending_observation_ids"]]}


def local_durability_corrected(detections_path: Path, output_dir: Path, tracking_config_path: Path | None = None) -> dict[str, Any]:
    """P2 corrected local policy over immutable P1 detections; detector-free."""
    records = [json.loads(line) for line in detections_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    config = json.loads(tracking_config_path.read_text(encoding="utf-8")) if tracking_config_path else dict(DEFAULT_TRACKING_CONFIG)
    thresholds = _thresholds(config)
    output_dir.mkdir(parents=True, exist_ok=False)
    ledger_positions = {int(record["position"]): record for record in records}
    tracks: list[dict[str, Any]] = []; active: dict[int, dict[str, Any]] = {}; alternatives: list[dict[str, Any]] = []; next_id = 0
    material_competition: set[int] = set(); material_pairs: list[dict[str, Any]] = []
    competition_margin = float(config.get("association_competition_margin", {}).get("value", 0.05))
    per_frame: list[dict[str, Any]] = []
    for record in records:
        observations = [_corrected_observation(record, thing, i) for i, thing in enumerate(record.get("things", []))]
        actual_current = [o for o in observations if o["is_actual"]]
        candidates: list[tuple[float, int, int, float, float]] = []
        for tid, track in active.items():
            previous = track["actual_anchor"]
            for oi, obs in enumerate(actual_current):
                cost, dc, ds = _association_cost(previous, obs)
                actual_gap = (obs["timestamp_ms"] - track["last_actual_ms"]) / 1000.0
                if dc <= thresholds["association_max_center_distance"] and ds <= thresholds["association_max_scale_log_distance"] and actual_gap <= thresholds["maximum_gap_duration"]:
                    candidates.append((cost, tid, oi, dc, ds))
                else:
                    alternatives.append({"frame_id": record["frame_id"], "track_id": tid, "observation_id": obs["observation_id"], "selected": False, "gated_out": True, "reason": "association_gate", "center_distance": dc, "scale_log_distance": ds})
        candidates.sort(key=lambda item: (item[0], item[1], item[2]))
        used_tracks: set[int] = set(); used_obs: set[int] = set(); matches: list[tuple[int, int, float, float, float]] = []
        for cost, tid, oi, dc, ds in candidates:
            if tid in used_tracks or oi in used_obs:
                continue
            used_tracks.add(tid); used_obs.add(oi); matches.append((tid, oi, cost, dc, ds))
        selected_pairs = {(tid, oi) for tid, oi, *_ in matches}
        for cost, tid, oi, dc, ds in candidates:
            selected = (tid, oi) in selected_pairs
            item = {"frame_id": record["frame_id"], "track_id": tid, "observation_id": actual_current[oi]["observation_id"], "selected": selected, "gated_out": False, "reason": "selected" if selected else "competing_identity", "cost": cost, "center_distance": dc, "scale_log_distance": ds}
            alternatives.append(item)
        for oi in range(len(actual_current)):
            choices = sorted([c for c in candidates if c[2] == oi], key=lambda c: c[0])
            if len(choices) > 1 and choices[1][0] - choices[0][0] <= competition_margin:
                material_competition.update((choices[0][1], choices[1][1]))
                material_pairs.append({"frame_id": actual_current[oi]["frame_id"], "observation_id": actual_current[oi]["observation_id"], "track_ids": [choices[0][1], choices[1][1]], "costs": [choices[0][0], choices[1][0]], "margin": choices[1][0] - choices[0][0], "rule": "unresolved_when_margin_leq_threshold"})
        for tid in {c[1] for c in candidates}:
            choices = sorted([c for c in candidates if c[1] == tid], key=lambda c: c[0])
            if len(choices) > 1 and choices[1][0] - choices[0][0] <= competition_margin:
                material_competition.add(tid)
                material_pairs.append({"frame_id": record["frame_id"], "track_ids": [tid], "observation_ids": [actual_current[c[2]]["observation_id"] for c in choices], "selected_observation_id": actual_current[next((m[1] for m in matches if m[0] == tid), choices[0][2])]["observation_id"], "costs": [c[0] for c in choices], "margin": choices[1][0] - choices[0][0], "rule": "one_track_multiple_successors_unresolved_when_margin_leq_threshold"})
        for tid, oi, cost, dc, ds in matches:
            obs = actual_current[oi]; obs["association"] = {"status": "matched", "center_distance": dc, "scale_log_distance": ds, "cost": cost}
            active[tid]["observations"].append(obs); active[tid]["actual_observations"].append(obs); active[tid]["actual_anchor"] = obs; active[tid]["last_actual_ms"] = obs["timestamp_ms"]
            used_obs.add(oi)
        for oi, obs in enumerate(actual_current):
            if oi not in used_obs:
                tid = next_id; next_id += 1; obs["association"] = {"status": "new"}
                active[tid] = {"track_id": tid, "observations": [obs], "actual_observations": [obs], "actual_anchor": obs, "non_actual_evidence": [], "last_actual_ms": obs["timestamp_ms"], "closed_reason": None}
        # Retain predictions/held evidence separately; it can never move an actual anchor.
        for obs in observations:
            if obs["is_actual"]:
                continue
            choices = [(float(np.linalg.norm(np.subtract(track["actual_anchor"]["center"], obs["center"]))), tid) for tid, track in active.items()]
            bounded = [(distance, tid) for distance, tid in choices if distance <= thresholds["association_max_center_distance"] and (obs["timestamp_ms"] - active[tid]["last_actual_ms"]) / 1000.0 <= thresholds["maximum_gap_duration"]]
            if len(bounded) == 1:
                distance, tid = bounded[0]; active[tid]["non_actual_evidence"].append(obs); active[tid]["observations"].append(obs); obs["association"] = {"status": "validated_non_actual_evidence", "track_id": tid, "distance_to_actual_anchor": distance, "anchor_frame_id": active[tid]["actual_anchor"]["frame_id"]}
            else:
                obs["association"] = {"status": "unattached_non_actual", "candidate_track_ids": [tid for _, tid in bounded]}
        for tid in list(active):
            last = active[tid]["last_actual_ms"]
            if (record["timestamp_ms"] - last) / 1000.0 > thresholds["maximum_gap_duration"]:
                active[tid]["closed_reason"] = "gap_exceeded"; active[tid]["closure_frame_id"] = record["frame_id"]; tracks.append(active.pop(tid))
        per_frame.append({"frame_id": record["frame_id"], "position": record["position"], "timestamp_ms": record["timestamp_ms"], "observations": observations, "emitted_observation_ids": [], "active_track_ids": sorted(active)})
    for tid, track in active.items():
        track["closed_reason"] = "end_of_ledger"; track["closure_frame_id"] = records[-1]["frame_id"] if records else None; tracks.append(track)
    decisions: list[dict[str, Any]] = []; emitted: list[dict[str, Any]] = []
    for track in tracks:
        all_obs = track["observations"]; actual = list(track["actual_observations"])
        if not actual:
            continue
        actual_positions = {o["position"] for o in actual}; start = min(actual_positions); end = max(actual_positions); eligible = end - start + 1
        unique_images = len({o.get("image_sha256") for o in actual}); gaps = []
        for left, right in zip(actual, actual[1:]):
            elapsed = (right["timestamp_ms"] - left["timestamp_ms"]) / 1000.0
            if right["position"] - left["position"] > 1 or elapsed > 0.15:
                gaps.append({"start_frame_id": left["frame_id"], "end_frame_id": right["frame_id"], "elapsed_s": elapsed, "unknown_reason": "no_actual_observation_in_interval", "closure_evidence": "next_actual_or_expiry" if elapsed <= thresholds["maximum_gap_duration"] else "expired"})
        local_evidence = _local_window_evidence(actual, thresholds["maximum_center_trend_residual"], thresholds["maximum_scale_change_residual"])
        center_residual = local_evidence["max_center_residual_l2"]; scale_residual = local_evidence["max_scale_residual_l2"]; offending = local_evidence["offending_observation_ids"]
        supported_fraction = unique_images / max(1, eligible)
        flow_values = [float(o.get("flow_support") or 0.0) for o in actual]
        shape_values = [float(o.get("shape_support") or 0.0) for o in actual]
        checks = {
            "minimum_independent_observations": {"measured": unique_images, "threshold": thresholds["minimum_independent_observations"], "passed": unique_images >= thresholds["minimum_independent_observations"]},
            "minimum_time_span": {"measured": (actual[-1]["timestamp_ms"] - actual[0]["timestamp_ms"]) / 1000.0, "threshold": thresholds["minimum_time_span"], "passed": (actual[-1]["timestamp_ms"] - actual[0]["timestamp_ms"]) / 1000.0 >= thresholds["minimum_time_span"]},
            "minimum_supported_fraction": {"measured": supported_fraction, "threshold": thresholds["minimum_supported_fraction"], "passed": supported_fraction >= thresholds["minimum_supported_fraction"]},
            "maximum_gap_duration": {"measured": max([g["elapsed_s"] for g in gaps] or [0.0]), "threshold": thresholds["maximum_gap_duration"], "passed": all(g["elapsed_s"] <= thresholds["maximum_gap_duration"] for g in gaps)},
            "maximum_local_center_trend_residual": {"measured": center_residual, "threshold": thresholds["maximum_center_trend_residual"], "passed": center_residual <= thresholds["maximum_center_trend_residual"]},
            "maximum_local_scale_residual": {"measured": scale_residual, "threshold": thresholds["maximum_scale_change_residual"], "passed": scale_residual <= thresholds["maximum_scale_change_residual"]},
            "minimum_flow_support": {"measured": min(flow_values or [0.0]), "threshold": thresholds["minimum_flow_support"], "passed": min(flow_values or [0.0]) >= thresholds["minimum_flow_support"]},
            "minimum_region_support": {"measured": min(shape_values or [0.0]), "threshold": thresholds.get("minimum_region_support", 0.25), "passed": min(shape_values or [0.0]) >= thresholds.get("minimum_region_support", 0.25)},
        }
        failed = [name for name, value in checks.items() if not value["passed"]]
        ambiguous = track["track_id"] in material_competition
        if unique_images < thresholds["minimum_independent_observations"] or not actual or ambiguous:
            verdict = "uncertain"
        elif any(name in failed for name in ("maximum_gap_duration", "maximum_local_center_trend_residual", "maximum_local_scale_residual")):
            verdict = "rejected"
        elif "minimum_region_support" in failed:
            verdict = "uncertain"
        else:
            verdict = "durable" if not failed else "uncertain"
        decision = {"track_id": f"track_{track['track_id']:04d}", "verdict": verdict, "decision_source": "local", "observations": all_obs, "actual_observation_ids": [o["observation_id"] for o in actual], "non_actual_observation_ids": [o["observation_id"] for o in all_obs if not o["is_actual"]], "visible_interval": {"start_frame_id": actual[0]["frame_id"], "end_frame_id": actual[-1]["frame_id"], "eligible_frame_count": eligible, "actual_frame_count": unique_images, "supported_fraction": supported_fraction}, "gaps": gaps, "closure": {"reason": track["closed_reason"], "frame_id": track.get("closure_frame_id")}, "metrics": {"local_center_residual": center_residual, "local_scale_residual": scale_residual, "minimum_flow_support": min(flow_values or [0.0]), "minimum_region_support": min(shape_values or [0.0])}, "local_window_evidence": local_evidence, "checks": checks, "failed_checks": failed, "offending_observation_ids": offending, "selected_associations": [a for a in alternatives if a.get("track_id") == track["track_id"] and a.get("selected")], "competing_associations": [a for a in alternatives if a.get("track_id") == track["track_id"] and not a.get("selected") and not a.get("gated_out")], "material_competition_evidence": [p for p in material_pairs if track["track_id"] in p["track_ids"]], "counterevidence": [{"type": "gated_or_competing_association", **item} for item in alternatives if item.get("track_id") == track["track_id"]], "prediction_count": sum(o["source_status"] == "predicted" for o in all_obs), "held_count": sum(o["source_status"] == "held" for o in all_obs), "ambiguity_policy": {"rule": "material competition is unresolved when second-best cost minus best cost is <= configured margin", "margin": competition_margin, "material": ambiguous}}
        decisions.append(decision)
        if verdict == "durable":
            for obs in actual:
                emitted.append({"frame_id": obs["frame_id"], "observation_id": obs["observation_id"], "track_id": decision["track_id"], "source_status": "observed", "verdict": "durable", "bbox_xyxy_norm": obs["bbox_xyxy_norm"], "detection_id": obs["detection_id"]})
    emitted_by_frame: dict[str, list[str]] = {}
    for item in emitted: emitted_by_frame.setdefault(item["frame_id"], []).append(item["observation_id"])
    for row in per_frame: row["emitted_observation_ids"] = emitted_by_frame.get(row["frame_id"], [])
    config_for_output = {**config, "association_competition_margin": {"value": competition_margin, "unit": "association_cost", "rationale": "near-tied assignments are conservatively unresolved for both implicated tracks"}, "minimum_region_support": {"value": float(config.get("minimum_region_support", {}).get("value", 0.25)), "unit": "shape_support_0_to_1"}, "rationale": "small fixed thresholds preserve evidence, count real frames, and bound local motion without a global straight-line assumption", "limitations": ["image-space identity is not semantic identity", "flow threshold zero is permissive and flow is supporting evidence only", "visibility is unknown between observations", "region support is generic shape evidence, not semantic identity"]}
    (output_dir / "tracking-config.json").write_text(json.dumps(config_for_output, indent=2, sort_keys=True), encoding="utf-8")
    (output_dir / "tracks.json").write_text(json.dumps({"schema_version": "durable_tracks_v2", "source_detections": str(detections_path), "source_detections_sha256": sha256(detections_path), "tracks": decisions, "association_alternatives": alternatives}, indent=2, sort_keys=True), encoding="utf-8")
    (output_dir / "decisions.json").write_text(json.dumps({"schema_version": "durability_decisions_v2", "policy": config, "decisions": decisions}, indent=2, sort_keys=True), encoding="utf-8")
    (output_dir / "emitted-boxes.jsonl").write_text("\n".join(json.dumps(item, sort_keys=True) for item in emitted) + ("\n" if emitted else ""), encoding="utf-8")
    (output_dir / "per-frame.json").write_text(json.dumps(per_frame, indent=2, sort_keys=True), encoding="utf-8")
    disputed = [item for item in alternatives if item.get("gated_out") or item.get("reason") == "competing_identity"]
    diagnostics = {"schema_version": "durability_diagnostics_v2", "trajectories": [{"track_id": d["track_id"], "verdict": d["verdict"], "points": [{"frame_id": o["frame_id"], "observation_id": o["observation_id"], "timestamp_ms": o["timestamp_ms"], "center": o["center"], "bbox_xyxy_norm": o["bbox_xyxy_norm"], "source_status": o["source_status"]} for o in d["observations"]], "local_window_evidence": d["local_window_evidence"], "gaps": d["gaps"], "closure": d["closure"]} for d in decisions], "disputed_spans": disputed, "material_competitions": material_pairs, "gap_spans": [g for d in decisions for g in d["gaps"]]}
    (output_dir / "diagnostics.json").write_text(json.dumps(diagnostics, indent=2, sort_keys=True), encoding="utf-8")
    canvas = np.full((900, 1400, 3), 255, dtype=np.uint8)
    colors = {"durable": (20, 130, 20), "rejected": (30, 30, 210), "uncertain": (0, 140, 210)}
    for index, decision in enumerate(decisions):
        points = [(int(o["center"][0] * 1000) + 200, int(o["center"][1] * 650) + 100) for o in decision["observations"]]
        for a, b in zip(points, points[1:]): cv2.line(canvas, a, b, colors[decision["verdict"]], 2)
        for point, obs in zip(points, decision["observations"]):
            cv2.circle(canvas, point, 5 if obs["source_status"] == "observed" else 8, colors[decision["verdict"]], -1 if obs["source_status"] == "observed" else 2)
            cv2.putText(canvas, obs["frame_id"], (point[0] + 5, point[1] - 5), cv2.FONT_HERSHEY_SIMPLEX, .32, colors[decision["verdict"]], 1, cv2.LINE_AA)
        if points: cv2.putText(canvas, f"{decision['track_id']} {decision['verdict']} actual={len(decision['actual_observation_ids'])} local={decision['metrics']['local_center_residual']:.3f}/{decision['metrics']['local_scale_residual']:.3f}", (20, 30 + index * 25), cv2.FONT_HERSHEY_SIMPLEX, .5, colors[decision["verdict"]], 1, cv2.LINE_AA)
    cv2.imwrite(str(output_dir / "trajectory-diagnostics.png"), canvas)
    panel_cols = 3; panel_rows = max(1, (len(decisions) + panel_cols - 1) // panel_cols)
    panel_canvas = np.full((max(900, 170 * panel_rows), 1800, 3), 255, dtype=np.uint8)
    for index, decision in enumerate(decisions):
        yoff = 90 + (index // panel_cols) * 170; xoff = 20 + (index % panel_cols) * 590
        cv2.rectangle(panel_canvas, (xoff, yoff - 45), (xoff + 540, yoff + 90), (210, 210, 210), 1)
        pts = [(xoff + 20 + int(o["center"][0] * 450), yoff + int(o["center"][1] * 70)) for o in decision["observations"]]
        for a, b in zip(pts, pts[1:]): cv2.line(panel_canvas, a, b, colors[decision["verdict"]], 2)
        for pt, obs in zip(pts, decision["observations"]):
            cv2.circle(panel_canvas, pt, 4 if obs["source_status"] == "observed" else 7, colors[decision["verdict"]], -1 if obs["source_status"] == "observed" else 2)
            cv2.putText(panel_canvas, obs["frame_id"], (pt[0], pt[1] - 7), cv2.FONT_HERSHEY_SIMPLEX, .3, (30, 30, 30), 1, cv2.LINE_AA)
        cv2.putText(panel_canvas, f"{decision['track_id']} {decision['verdict']} center/scale thresholds .12/.35", (xoff, yoff - 25), cv2.FONT_HERSHEY_SIMPLEX, .38, colors[decision["verdict"]], 1, cv2.LINE_AA)
    cv2.imwrite(str(output_dir / "track-panels.png"), panel_canvas)
    obs_lookup = {o["observation_id"]: o for d in decisions for o in d["observations"]}
    panel_height = max(900, 260 * max(1, len(disputed))); disputed_canvas = np.full((panel_height, 1600, 3), 255, dtype=np.uint8)
    cv2.putText(disputed_canvas, f"disputed coverage: {len(disputed)}/{len(alternatives)} alternatives; full JSON retained", (20, 30), cv2.FONT_HERSHEY_SIMPLEX, .55, (0, 0, 0), 1, cv2.LINE_AA)
    for index, item in enumerate(disputed):
        yoff = 100 + index * 260; obs = obs_lookup.get(item.get("observation_id"));
        if obs:
            point = (100 + int(obs["center"][0] * 700), yoff + int(obs["center"][1] * 120)); cv2.rectangle(disputed_canvas, (100, yoff), (900, yoff + 150), (210, 210, 210), 1); cv2.circle(disputed_canvas, point, 8, (0, 0, 220), -1)
            cv2.putText(disputed_canvas, item.get("observation_id", ""), (point[0] + 8, point[1] - 8), cv2.FONT_HERSHEY_SIMPLEX, .4, (0, 0, 120), 1, cv2.LINE_AA)
        label = f"#{index} frame={item.get('frame_id')} obs={item.get('observation_id')} track={item.get('track_id')} reason={item.get('reason')} selected={item.get('selected')} cost={item.get('cost', 'n/a')} margin={competition_margin}"
        cv2.putText(disputed_canvas, label, (20, yoff + 190), cv2.FONT_HERSHEY_SIMPLEX, .45, (0, 0, 180), 1, cv2.LINE_AA)
    cv2.imwrite(str(output_dir / "disputed-spans.png"), disputed_canvas)
    (output_dir / "diagnostic-index.json").write_text(json.dumps({"alternative_count": len(alternatives), "disputed_count": len(disputed), "rendered_dispute_count": len(disputed), "coverage": "all disputed alternatives rendered; complete alternatives in diagnostics.json and tracks.json", "track_panel_count": len(decisions), "references": [{"index": i, "frame_id": item.get("frame_id"), "observation_id": item.get("observation_id"), "track_id": item.get("track_id"), "reason": item.get("reason")} for i, item in enumerate(disputed)]}, indent=2, sort_keys=True), encoding="utf-8")
    return {"track_count": len(decisions), "verdict_counts": {v: sum(d["verdict"] == v for d in decisions) for v in ("durable", "rejected", "uncertain")}, "frame_count": len(per_frame), "input_observation_count": sum(len(r.get("things", [])) for r in records), "emitted_count": len(emitted), "prediction_count": sum(1 for r in records for t in r.get("things", []) if str((t.get("properties") or {}).get("track_event", "")).lower() == "predicted"), "held_count": sum(1 for r in records for t in r.get("things", []) if str((t.get("properties") or {}).get("track_event", "")).lower() == "held")}
