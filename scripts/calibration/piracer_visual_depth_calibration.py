#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from implementations.perception.features import FeatureMatch, detect_keypoints, grayscale, match_keypoints, track_features
from implementations.perception.motion import SceneMotionResult, analyze_scene_motion, find_motion_groups


@dataclass
class RobotCommand:
    throttle: float
    steering: float
    duration_s: float
    settle_s: float


@dataclass
class CaptureFrame:
    frame_id: str
    image_path: str
    timestamp_ms: int
    command_before_frame: dict[str, float] | None


@dataclass
class CaptureRun:
    run_id: str
    run_type: str
    frames: list[CaptureFrame]
    metadata: dict[str, Any]


def now_id(prefix: str) -> str:
    return f"{prefix}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"


def timestamp_ms() -> int:
    return int(time.time() * 1000)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def command_dict(command: RobotCommand) -> dict[str, float]:
    return {
        "throttle": float(command.throttle),
        "steering": float(command.steering),
        "duration_s": float(command.duration_s),
        "settle_s": float(command.settle_s),
    }


def command_phase_dict(command: RobotCommand, phase: str, cycle: int) -> dict[str, Any]:
    data: dict[str, Any] = command_dict(command)
    data["phase"] = phase
    data["cycle"] = cycle
    return data


def endpoint_url(base_url: str, endpoint: str) -> str:
    return f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"


def http_get_bytes(url: str, timeout_s: float) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as response:
            return response.read()
    except urllib.error.URLError as exc:
        raise RuntimeError(f"GET failed for {url}: {exc}") from exc


def http_post_json(url: str, payload: dict[str, Any], timeout_s: float) -> None:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            response.read()
    except urllib.error.URLError as exc:
        raise RuntimeError(f"POST failed for {url}: {exc}") from exc


def post_drive(base_url: str, steering: float, throttle: float) -> None:
    payload = {
        "angle": float(steering),
        "steering": float(steering),
        "throttle": float(throttle),
        "drive_mode": "user",
        "recording": False,
    }
    http_post_json(endpoint_url(base_url, "/drive"), payload, timeout_s=2.0)


def capture_frame(base_url: str, endpoint: str, path: Path) -> None:
    path.write_bytes(http_get_bytes(endpoint_url(base_url, endpoint), timeout_s=10.0))


def collect_pulse_run(
    *,
    run_type: str,
    base_url: str,
    frame_endpoint: str,
    pulses: int,
    command: RobotCommand,
    out_dir: Path,
    camera_mount: str,
    robot_id: str,
    environment_id: str,
) -> CaptureRun:
    out_dir.mkdir(parents=True, exist_ok=True)
    frames: list[CaptureFrame] = []

    try:
        post_drive(base_url, 0.0, 0.0)
        time.sleep(command.settle_s)
        frames.append(capture_run_frame(0, base_url, frame_endpoint, out_dir, None))

        for index in range(1, pulses + 1):
            post_drive(base_url, command.steering, command.throttle)
            time.sleep(command.duration_s)
            post_drive(base_url, 0.0, 0.0)
            time.sleep(command.settle_s)
            frames.append(capture_run_frame(index, base_url, frame_endpoint, out_dir, command_dict(command)))
    finally:
        post_drive(base_url, 0.0, 0.0)

    first_image = out_dir / frames[0].image_path
    width, height = Image.open(first_image).size
    run = CaptureRun(
        run_id=out_dir.name,
        run_type=run_type,
        frames=frames,
        metadata={
            "image_width_px": width,
            "image_height_px": height,
            "camera_mount": camera_mount,
            "robot_id": robot_id,
            "environment_id": environment_id,
            "base_url": base_url,
            "frame_endpoint": frame_endpoint,
            "created_at_ms": timestamp_ms(),
        },
    )
    write_json(out_dir / "capture_run.json", asdict(run))
    return run


def collect_reciprocal_run(
    *,
    base_url: str,
    frame_endpoint: str,
    cycles: int,
    forward_command: RobotCommand,
    reverse_command: RobotCommand,
    out_dir: Path,
    camera_mount: str,
    robot_id: str,
    environment_id: str,
) -> CaptureRun:
    out_dir.mkdir(parents=True, exist_ok=True)
    frames: list[CaptureFrame] = []
    frame_index = 0

    try:
        post_drive(base_url, 0.0, 0.0)
        time.sleep(forward_command.settle_s)
        frames.append(capture_run_frame(frame_index, base_url, frame_endpoint, out_dir, None))

        for cycle in range(1, cycles + 1):
            frame_index += 1
            post_drive(base_url, forward_command.steering, forward_command.throttle)
            time.sleep(forward_command.duration_s)
            post_drive(base_url, 0.0, 0.0)
            time.sleep(forward_command.settle_s)
            frames.append(capture_run_frame(
                frame_index,
                base_url,
                frame_endpoint,
                out_dir,
                command_phase_dict(forward_command, "forward", cycle),
            ))

            frame_index += 1
            post_drive(base_url, reverse_command.steering, reverse_command.throttle)
            time.sleep(reverse_command.duration_s)
            post_drive(base_url, 0.0, 0.0)
            time.sleep(reverse_command.settle_s)
            frames.append(capture_run_frame(
                frame_index,
                base_url,
                frame_endpoint,
                out_dir,
                command_phase_dict(reverse_command, "reverse", cycle),
            ))
    finally:
        post_drive(base_url, 0.0, 0.0)

    first_image = out_dir / frames[0].image_path
    width, height = Image.open(first_image).size
    run = CaptureRun(
        run_id=out_dir.name,
        run_type="reciprocal_drift",
        frames=frames,
        metadata={
            "image_width_px": width,
            "image_height_px": height,
            "camera_mount": camera_mount,
            "robot_id": robot_id,
            "environment_id": environment_id,
            "base_url": base_url,
            "frame_endpoint": frame_endpoint,
            "created_at_ms": timestamp_ms(),
            "cycles": cycles,
            "forward_command": command_dict(forward_command),
            "reverse_command": command_dict(reverse_command),
        },
    )
    write_json(out_dir / "capture_run.json", asdict(run))
    return run


def capture_run_frame(
    index: int,
    base_url: str,
    endpoint: str,
    out_dir: Path,
    command_before_frame: dict[str, float] | None,
) -> CaptureFrame:
    image_name = f"frame_{index:03d}.jpg"
    capture_frame(base_url, endpoint, out_dir / image_name)
    return CaptureFrame(
        frame_id=f"frame_{index:03d}",
        image_path=image_name,
        timestamp_ms=timestamp_ms(),
        command_before_frame=command_before_frame,
    )


def load_capture_run(path: Path) -> tuple[dict[str, Any], Path]:
    manifest = read_json(path)
    return manifest, path.parent


def image_paths_from_run(manifest: dict[str, Any], run_dir: Path) -> list[Path]:
    paths: list[Path] = []
    for frame in manifest["frames"]:
        image_path = Path(frame["image_path"])
        if not image_path.is_absolute():
            image_path = run_dir / image_path
        paths.append(image_path)
    return paths


def image_paths_from_args(args: argparse.Namespace) -> list[Path]:
    if args.images:
        return [Path(path) for path in args.images]
    if not args.image_dir:
        raise SystemExit("provide image paths or --image-dir")
    return sorted(Path(args.image_dir).glob(args.pattern))


def synthetic_capture_run(
    image_paths: list[Path],
    *,
    run_type: str,
    command: RobotCommand,
    robot_id: str,
    environment_id: str,
) -> dict[str, Any]:
    if not image_paths:
        raise SystemExit("need at least one image")
    width, height = Image.open(image_paths[0]).size
    frames = []
    for index, image_path in enumerate(image_paths):
        frames.append({
            "frame_id": f"frame_{index:03d}",
            "image_path": str(image_path),
            "timestamp_ms": 0,
            "command_before_frame": None if index == 0 else command_dict(command),
        })
    return {
        "run_id": f"synthetic-{now_id(run_type)}",
        "run_type": run_type,
        "frames": frames,
        "metadata": {
            "image_width_px": width,
            "image_height_px": height,
            "camera_mount": "piracer_fixed_front",
            "robot_id": robot_id,
            "environment_id": environment_id,
            "created_at_ms": timestamp_ms(),
            "source": "image_paths",
        },
    }


def default_camera_model(width: int, height: int, hfov_deg: float, vfov_deg: float) -> dict[str, Any]:
    fx = (width / 2.0) / math.tan(math.radians(hfov_deg) / 2.0)
    fy = (height / 2.0) / math.tan(math.radians(vfov_deg) / 2.0)
    return {
        "intrinsics": {
            "image_width_px": width,
            "image_height_px": height,
            "fx_px": fx,
            "fy_px": fy,
            "cx_px": width / 2.0,
            "cy_px": height / 2.0,
            "distortion": {
                "model": "opencv_pinhole",
                "k1": 0.0,
                "k2": 0.0,
                "p1": 0.0,
                "p2": 0.0,
                "k3": 0.0,
            },
            "fov": {
                "horizontal_deg": hfov_deg,
                "vertical_deg": vfov_deg,
            },
            "source": "default_fov_prior",
        },
        "extrinsics": {
            "camera_height_steps": None,
            "camera_height_meters": None,
            "pitch_down_deg": None,
            "yaw_offset_deg": 0.0,
            "roll_deg": 0.0,
        },
        "quality": {
            "reprojection_error_px_mean": None,
            "line_straightness_error_px_mean": None,
            "confidence": 0.25,
            "notes": [
                "Phase-1 calibration uses an FOV prior. Trusted intrinsics require known geometry or a calibration target.",
            ],
        },
    }


def classify_group(group: dict[str, Any], dominant_id: int | None, near_scale_threshold: float) -> dict[str, Any]:
    scale = group.get("scale")
    is_nearer = scale is not None and float(scale) >= near_scale_threshold
    return {
        "group_id": group["group_id"],
        "role": "dominant_scene" if group["group_id"] == dominant_id else "secondary_scene",
        "depth_hint": "nearer_candidate" if is_nearer else "same_depth_or_background",
        "match_count": group["match_count"],
        "source_bbox": group["source_bbox"],
        "target_bbox": group["target_bbox"],
        "center_shift_px": group["center_shift_px"],
        "median_motion_px": group["median_motion_px"],
        "scale": scale,
        "median_residual_px": group["median_residual_px"],
        "mean_score": group["mean_score"],
        "kind_hint": group["kind_hint"],
    }


def summarize_scene_motion(result: SceneMotionResult, near_scale_threshold: float) -> dict[str, Any]:
    data = asdict(result)
    dominant_id = None
    if data["groups"]:
        dominant_id = max(data["groups"], key=lambda item: item["match_count"])["group_id"]
    groups = [classify_group(group, dominant_id, near_scale_threshold) for group in data["groups"]]
    match_ratio = data["match_count"] / max(data["keypoint_count"], 1)
    grouped_ratio = data["grouped_match_count"] / max(data["match_count"], 1)
    return {
        "image_a": data["image_a"],
        "image_b": data["image_b"],
        "roi": data["roi"],
        "keypoint_count": data["keypoint_count"],
        "match_count": data["match_count"],
        "grouped_match_count": data["grouped_match_count"],
        "ungrouped_match_count": data["ungrouped_match_count"],
        "match_ratio": match_ratio,
        "grouped_ratio": grouped_ratio,
        "groups": groups,
        "output_files": data["output_files"],
    }


def fit_similarity_transform(matches: list[FeatureMatch]) -> tuple[complex, complex, float] | None:
    if len(matches) < 2:
        return None
    source = np.array([match.source for match in matches], dtype=np.float64)
    target = np.array([match.target for match in matches], dtype=np.float64)
    source_center = source.mean(axis=0)
    target_center = target.mean(axis=0)
    src = (source[:, 0] - source_center[0]) + 1j * (source[:, 1] - source_center[1])
    dst = (target[:, 0] - target_center[0]) + 1j * (target[:, 1] - target_center[1])
    denominator = np.sum(np.abs(src) ** 2)
    if denominator < 1e-6:
        return None
    transform = np.sum(dst * np.conj(src)) / denominator
    offset = (target_center[0] + 1j * target_center[1]) - transform * (
        source_center[0] + 1j * source_center[1]
    )
    return transform, offset, float(abs(transform))


def predict_with_transform(transform: tuple[complex, complex, float], point: list[float]) -> list[float]:
    z = complex(float(point[0]), float(point[1]))
    predicted = transform[0] * z + transform[1]
    return [float(predicted.real), float(predicted.imag)]


def residual_px(predicted: list[float], observed: list[float]) -> float:
    return float(math.hypot(predicted[0] - observed[0], predicted[1] - observed[1]))


def split_train_holdout(matches: list[FeatureMatch], holdout_stride: int) -> tuple[list[FeatureMatch], list[FeatureMatch]]:
    stride = max(2, int(holdout_stride))
    train: list[FeatureMatch] = []
    holdout: list[FeatureMatch] = []
    for index, match in enumerate(matches):
        if index % stride == 0:
            holdout.append(match)
        else:
            train.append(match)
    return train, holdout


def score_from_residuals(residuals: list[float], threshold_px: float) -> tuple[float, float, float, float]:
    if not residuals:
        return 0.0, 0.0, 0.0, 0.0
    arr = np.asarray(residuals, dtype=np.float64)
    median_error = float(np.median(arr))
    p95_error = float(np.percentile(arr, 95))
    inlier_ratio = float((arr <= threshold_px).mean())
    median_score = max(0.0, min(1.0, 1.0 - median_error / max(threshold_px * 2.0, 1e-6)))
    p95_score = max(0.0, min(1.0, 1.0 - p95_error / max(threshold_px * 4.0, 1e-6)))
    score = 0.55 * inlier_ratio + 0.30 * median_score + 0.15 * p95_score
    return float(score), median_error, p95_error, inlier_ratio


def point_in_xyxy_box(point: list[float], box: list[int] | None, margin_px: float) -> bool:
    if box is None:
        return False
    x0, y0, x1, y1 = box
    x, y = float(point[0]), float(point[1])
    return (x0 - margin_px) <= x <= (x1 + margin_px) and (y0 - margin_px) <= y <= (y1 + margin_px)


def point_in_any_box(point: list[float], boxes: list[list[int]], margin_px: float) -> bool:
    return any(point_in_xyxy_box(point, box, margin_px) for box in boxes)


def metric_block(residuals: list[float], threshold_px: float) -> dict[str, Any]:
    score, median_error, p95_error, inlier_ratio = score_from_residuals(residuals, threshold_px)
    return {
        "score": score,
        "predicted_count": len(residuals),
        "median_error_px": median_error if residuals else None,
        "p95_error_px": p95_error if residuals else None,
        "mean_error_px": float(np.mean(residuals)) if residuals else None,
        "inlier_ratio_under_threshold": inlier_ratio,
    }


def backtest_pair(
    image_a_path: Path,
    image_b_path: Path,
    out_dir: Path,
    pair_index: int,
    args: argparse.Namespace,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    image_a = Image.open(image_a_path).convert("RGB")
    image_b = Image.open(image_b_path).convert("RGB")
    width, height = image_a.size
    roi = [0, 0, width - 1, height - 1]
    gray_a = grayscale(image_a)
    gray_b = grayscale(image_b)
    keypoints = detect_keypoints(
        gray_a,
        roi,
        max_features=args.max_features,
        min_distance=args.min_distance,
        patch_radius=args.patch_radius,
    )
    matches = match_keypoints(
        gray_a,
        gray_b,
        keypoints,
        patch_radius=args.patch_radius,
        search_radius=args.search_radius,
        min_score=args.min_score,
    )
    train, holdout = split_train_holdout(matches, args.backtest_holdout_stride)
    if len(train) < args.backtest_min_train_matches or len(holdout) < args.backtest_min_holdout_matches:
        return {
            "pair": [pair_index, pair_index + 1],
            "image_a": str(image_a_path),
            "image_b": str(image_b_path),
            "status": "insufficient_matches",
            "keypoint_count": len(keypoints),
            "match_count": len(matches),
            "train_count": len(train),
            "holdout_count": len(holdout),
            "hypothesis_count": 0,
            "score": 0.0,
            "median_error_px": None,
            "p95_error_px": None,
            "mean_error_px": None,
            "inlier_ratio_under_threshold": 0.0,
            "threshold_px": args.backtest_residual_threshold,
            "hypotheses": [],
            "output_files": {},
        }

    groups, grouped_indices = find_motion_groups(
        train,
        max_groups=args.max_groups,
        min_group_size=args.min_group_size,
        residual_threshold=args.residual_threshold,
    )
    hypotheses: list[dict[str, Any]] = []
    global_transform = fit_similarity_transform(train)
    if global_transform is not None:
        hypotheses.append({
            "id": -1,
            "kind": "global_motion",
            "match_count": len(train),
            "source_bbox": None,
            "target_bbox": None,
            "scale": global_transform[2],
            "depth_hint": "nearer_candidate" if global_transform[2] >= args.near_scale_threshold else "same_depth_or_background",
            "transform": global_transform,
        })
    for group, indices in zip(groups, grouped_indices):
        group_matches = [train[index] for index in indices]
        transform = fit_similarity_transform(group_matches)
        if transform is None:
            continue
        hypotheses.append({
            "id": group.group_id,
            "kind": "motion_group",
            "match_count": group.match_count,
            "source_bbox": group.source_bbox,
            "target_bbox": group.target_bbox,
            "scale": transform[2],
            "depth_hint": "nearer_candidate" if transform[2] >= args.near_scale_threshold else "same_depth_or_background",
            "transform": transform,
        })

    candidate_boxes = [
        hypothesis["source_bbox"]
        for hypothesis in hypotheses
        if hypothesis["kind"] == "motion_group" and hypothesis["source_bbox"] is not None
    ]
    nearer_boxes = [
        hypothesis["source_bbox"]
        for hypothesis in hypotheses
        if (
            hypothesis["kind"] == "motion_group"
            and hypothesis["source_bbox"] is not None
            and hypothesis["depth_hint"] == "nearer_candidate"
        )
    ]

    records: list[dict[str, Any]] = []
    residuals: list[float] = []
    for match in holdout:
        best: tuple[float, dict[str, Any], list[float]] | None = None
        for hypothesis in hypotheses:
            predicted = predict_with_transform(hypothesis["transform"], match.source)
            error = residual_px(predicted, match.target)
            if best is None or error < best[0]:
                best = (error, hypothesis, predicted)
        if best is None:
            continue
        error, hypothesis, predicted = best
        residuals.append(error)
        in_candidate_region = point_in_any_box(match.source, candidate_boxes, args.backtest_region_margin)
        in_nearer_region = point_in_any_box(match.source, nearer_boxes, args.backtest_region_margin)
        region = (
            "nearer_candidate_region"
            if in_nearer_region
            else "motion_group_region"
            if in_candidate_region
            else "background_or_floor_region"
        )
        records.append({
            "source": [round(float(match.source[0]), 3), round(float(match.source[1]), 3)],
            "observed": [round(float(match.target[0]), 3), round(float(match.target[1]), 3)],
            "predicted": [round(float(predicted[0]), 3), round(float(predicted[1]), 3)],
            "residual_px": round(float(error), 4),
            "hypothesis_id": hypothesis["id"],
            "hypothesis_kind": hypothesis["kind"],
            "region": region,
            "inlier": error <= args.backtest_residual_threshold,
        })

    full_frame_metrics = metric_block(residuals, args.backtest_residual_threshold)
    candidate_records = [
        record
        for record in records
        if record["region"] in {"motion_group_region", "nearer_candidate_region"}
    ]
    candidate_residuals = [float(record["residual_px"]) for record in candidate_records]
    candidate_metrics = metric_block(candidate_residuals, args.backtest_residual_threshold)
    nearer_records = [record for record in records if record["region"] == "nearer_candidate_region"]
    nearer_residuals = [float(record["residual_px"]) for record in nearer_records]
    nearer_metrics = metric_block(nearer_residuals, args.backtest_residual_threshold)

    if candidate_metrics["predicted_count"] >= args.backtest_min_candidate_records:
        selected_metrics = candidate_metrics
        score_basis = "motion_group_regions"
    else:
        selected_metrics = full_frame_metrics
        score_basis = "full_frame_fallback"

    overlay_path = out_dir / "backtest_prediction.jpg"
    render_backtest_overlay(
        image_a,
        image_b,
        records,
        args.backtest_residual_threshold,
        overlay_path,
    )
    candidate_overlay_path = out_dir / "backtest_candidate_prediction.jpg"
    render_backtest_overlay(
        image_a,
        image_b,
        candidate_records,
        args.backtest_residual_threshold,
        candidate_overlay_path,
    )
    serializable_hypotheses = []
    for hypothesis in hypotheses:
        transform, offset, scale = hypothesis["transform"]
        serializable_hypotheses.append({
            key: value
            for key, value in {
                **{k: v for k, v in hypothesis.items() if k != "transform"},
                "transform": {
                    "a_real": float(transform.real),
                    "a_imag": float(transform.imag),
                    "offset_real": float(offset.real),
                    "offset_imag": float(offset.imag),
                    "scale": float(scale),
                },
            }.items()
        })

    return {
        "pair": [pair_index, pair_index + 1],
        "image_a": str(image_a_path),
        "image_b": str(image_b_path),
        "status": "ok" if records else "no_predictable_holdout",
        "keypoint_count": len(keypoints),
        "match_count": len(matches),
        "train_count": len(train),
        "holdout_count": len(holdout),
        "predicted_count": len(records),
        "hypothesis_count": len(hypotheses),
        "score": selected_metrics["score"],
        "score_basis": score_basis,
        "median_error_px": selected_metrics["median_error_px"],
        "p95_error_px": selected_metrics["p95_error_px"],
        "mean_error_px": selected_metrics["mean_error_px"],
        "inlier_ratio_under_threshold": selected_metrics["inlier_ratio_under_threshold"],
        "full_frame": full_frame_metrics,
        "motion_group_regions": candidate_metrics,
        "nearer_candidate_regions": nearer_metrics,
        "threshold_px": args.backtest_residual_threshold,
        "region_margin_px": args.backtest_region_margin,
        "hypotheses": serializable_hypotheses,
        "records": records,
        "output_files": {
            "overlay": str(overlay_path),
            "candidate_overlay": str(candidate_overlay_path),
        },
    }


def render_backtest_overlay(
    image_a: Image.Image,
    image_b: Image.Image,
    records: list[dict[str, Any]],
    threshold_px: float,
    out_path: Path,
) -> None:
    width, height = image_a.size
    canvas = Image.new("RGB", (width * 2, max(height, image_b.height)), (18, 20, 24))
    canvas.paste(image_a, (0, 0))
    canvas.paste(image_b, (width, 0))
    draw = ImageDraw.Draw(canvas)
    draw.text((8, 8), "source", fill=(255, 255, 255))
    draw.text((width + 8, 8), "target: held-out prediction vs observed", fill=(255, 255, 255))

    sorted_records = sorted(records, key=lambda item: item["residual_px"], reverse=True)
    for record in sorted_records[:180]:
        sx, sy = record["source"]
        ox, oy = record["observed"]
        px, py = record["predicted"]
        inlier = record["residual_px"] <= threshold_px
        color = (80, 220, 120) if inlier else (245, 90, 80)
        prediction_color = (90, 190, 255)
        draw.line([(sx, sy), (px + width, py)], fill=color, width=1)
        draw.line([(px + width, py), (ox + width, oy)], fill=prediction_color, width=1)
        draw.ellipse([sx - 2, sy - 2, sx + 2, sy + 2], fill=color)
        draw.rectangle([px + width - 2, py - 2, px + width + 2, py + 2], outline=prediction_color, width=1)
        draw.ellipse([ox + width - 2, oy - 2, ox + width + 2, oy + 2], fill=color)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, quality=92)


def backtest_depth_run(image_paths: list[Path], out_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    pairs: list[dict[str, Any]] = []
    all_residuals: list[float] = []
    all_candidate_residuals: list[float] = []
    all_nearer_residuals: list[float] = []
    for index, (image_a, image_b) in enumerate(zip(image_paths, image_paths[1:])):
        pair = backtest_pair(image_a, image_b, out_dir / f"{index:02d}_{index + 1:02d}", index, args)
        pairs.append(pair)
        for record in pair.get("records", []):
            all_residuals.append(float(record["residual_px"]))
            if record.get("region") in {"motion_group_region", "nearer_candidate_region"}:
                all_candidate_residuals.append(float(record["residual_px"]))
            if record.get("region") == "nearer_candidate_region":
                all_nearer_residuals.append(float(record["residual_px"]))

    valid_pairs = [pair for pair in pairs if pair["status"] == "ok" and pair.get("predicted_count", 0) > 0]
    if valid_pairs:
        total_predicted = sum(
            max(
                pair.get("motion_group_regions", {}).get("predicted_count", 0),
                pair.get("predicted_count", 0) if pair.get("score_basis") == "full_frame_fallback" else 0,
            )
            for pair in valid_pairs
        )
        overall_score = sum(
            pair["score"] * max(
                pair.get("motion_group_regions", {}).get("predicted_count", 0),
                pair.get("predicted_count", 0) if pair.get("score_basis") == "full_frame_fallback" else 0,
            )
            for pair in valid_pairs
        ) / max(total_predicted, 1)
    else:
        overall_score = 0.0

    full_frame_metrics = metric_block(all_residuals, args.backtest_residual_threshold)
    candidate_metrics = metric_block(all_candidate_residuals, args.backtest_residual_threshold)
    nearer_metrics = metric_block(all_nearer_residuals, args.backtest_residual_threshold)

    result = {
        "method": "heldout_feature_prediction",
        "description": "Fit motion hypotheses on train feature matches, predict held-out feature locations, and score pixel residuals. Overall score prefers held-out features inside motion-group regions so uninteresting floor/carpet features do not dominate.",
        "pair_count": len(pairs),
        "valid_pair_count": len(valid_pairs),
        "overall_score": float(overall_score),
        "score_basis": "motion_group_regions",
        "median_error_px": candidate_metrics["median_error_px"],
        "p95_error_px": candidate_metrics["p95_error_px"],
        "mean_error_px": candidate_metrics["mean_error_px"],
        "inlier_ratio_under_threshold": candidate_metrics["inlier_ratio_under_threshold"],
        "full_frame": full_frame_metrics,
        "motion_group_regions": candidate_metrics,
        "nearer_candidate_regions": nearer_metrics,
        "threshold_px": args.backtest_residual_threshold,
        "holdout_stride": args.backtest_holdout_stride,
        "region_margin_px": args.backtest_region_margin,
        "pairs": pairs,
        "output_files": {
            "summary": str(out_dir / "backtest.json"),
        },
    }
    write_json(out_dir / "backtest.json", result)
    return result


def analyze_depth_run(
    *,
    manifest: dict[str, Any],
    run_dir: Path,
    image_paths: list[Path],
    out_dir: Path,
    command: RobotCommand,
    args: argparse.Namespace,
) -> dict[str, Any]:
    if len(image_paths) < 2:
        raise SystemExit("need at least two images to analyze motion depth")

    out_dir.mkdir(parents=True, exist_ok=True)
    pair_summaries: list[dict[str, Any]] = []
    for index, (image_a, image_b) in enumerate(zip(image_paths, image_paths[1:])):
        pair_dir = out_dir / f"{index:02d}_{index + 1:02d}"
        result = analyze_scene_motion(
            image_a,
            image_b,
            pair_dir,
            max_features=args.max_features,
            min_distance=args.min_distance,
            patch_radius=args.patch_radius,
            search_radius=args.search_radius,
            min_score=args.min_score,
            max_groups=args.max_groups,
            min_group_size=args.min_group_size,
            residual_threshold=args.residual_threshold,
        )
        pair_summaries.append(summarize_scene_motion(result, args.near_scale_threshold))

    width, height = Image.open(image_paths[0]).size
    camera_model = default_camera_model(width, height, args.hfov_deg, args.vfov_deg)
    backtest = None if args.no_backtest else backtest_depth_run(image_paths, out_dir / "backtest", args)
    calibration = build_calibration_bundle(manifest, command, camera_model, pair_summaries, backtest, args)

    scene_depth_path = out_dir / "scene_depth_summary.json"
    calibration_path = out_dir / "calibration.json"
    html_path = out_dir / "index.html"
    write_json(scene_depth_path, {
        "capture_run": manifest,
        "source_run_dir": str(run_dir),
        "pair_count": len(pair_summaries),
        "pairs": pair_summaries,
        "backtest": backtest,
    })
    write_json(calibration_path, calibration)
    write_html_report(html_path, manifest, image_paths, pair_summaries, calibration, backtest)

    return {
        "out_dir": str(out_dir),
        "scene_depth_summary": str(scene_depth_path),
        "calibration": str(calibration_path),
        "html": str(html_path),
        "pair_count": len(pair_summaries),
        "pairs": pair_summaries,
        "backtest": backtest,
        "calibration_bundle": calibration,
    }


def build_calibration_bundle(
    manifest: dict[str, Any],
    command: RobotCommand,
    camera_model: dict[str, Any],
    pairs: list[dict[str, Any]],
    backtest: dict[str, Any] | None,
    args: argparse.Namespace,
) -> dict[str, Any]:
    grouped_ratios = [pair["grouped_ratio"] for pair in pairs]
    match_counts = [pair["match_count"] for pair in pairs]
    near_groups = [
        group
        for pair in pairs
        for group in pair["groups"]
        if group["depth_hint"] == "nearer_candidate"
    ]
    mean_grouped_ratio = sum(grouped_ratios) / len(grouped_ratios) if grouped_ratios else 0.0
    mean_match_count = sum(match_counts) / len(match_counts) if match_counts else 0.0
    match_score = min(1.0, mean_match_count / max(args.max_features * 0.55, 1.0))
    support_confidence = max(0.0, min(1.0, 0.45 * mean_grouped_ratio + 0.35 * match_score + 0.20 * min(1.0, len(near_groups) / max(len(pairs), 1))))
    backtest_score = None if backtest is None else float(backtest["overall_score"])
    confidence = support_confidence if backtest_score is None else 0.55 * support_confidence + 0.45 * backtest_score

    return {
        "created_at_ms": timestamp_ms(),
        "robot_id": manifest.get("metadata", {}).get("robot_id", args.robot_id),
        "environment_id": manifest.get("metadata", {}).get("environment_id", args.environment_id),
        "phase": "visual_depth_step_calibration_v0",
        "limits": {
            "metric_scale": "nominal pulse is the unit; physical meters are not estimated",
            "intrinsics": "default FOV prior unless replaced by target-based calibration",
            "observations": "feature-motion depth layers, not semantic object detections",
        },
        "camera_model": camera_model,
        "step_unit": {
            "name": "nominal_forward_pulse",
            "command": command_dict(command),
            "definition": (
                f"One pulse at throttle {command.throttle} steering {command.steering} "
                f"for {command.duration_s} seconds."
            ),
            "mean_delta_forward_steps": 1.0,
            "std_delta_forward_steps": None,
            "mean_lateral_slip_steps": None,
            "mean_yaw_drift_deg": None,
            "confidence": confidence,
        },
        "scene_depth": {
            "pair_count": len(pairs),
            "mean_match_count": mean_match_count,
            "mean_grouped_ratio": mean_grouped_ratio,
            "nearer_candidate_count": len(near_groups),
            "support_confidence": support_confidence,
            "pairs": pairs,
        },
        "backtest": compact_backtest_summary(backtest),
        "turn_radius_table": [],
        "validation": {
            "heldout_runs": [],
            "mean_reprojection_error_px": None if backtest is None else backtest["mean_error_px"],
            "mean_bbox_iou": None,
            "pose_drift_per_step": None,
            "turn_prediction_error_deg": None,
            "feature_prediction_error_px_median": None if backtest is None else backtest["median_error_px"],
            "feature_prediction_error_px_p95": None if backtest is None else backtest["p95_error_px"],
            "feature_prediction_inlier_ratio": None if backtest is None else backtest["inlier_ratio_under_threshold"],
            "backtest_score": backtest_score,
            "confidence": confidence if backtest_score is None else backtest_score,
            "notes": [
                "This phase validates held-out feature prediction only. Pose and turn-radius validation are not solved yet.",
                "Backtest uses the same capture run but holds out feature matches from each pair before fitting motion hypotheses.",
            ],
        },
        "confidence": {
            "camera_intrinsics": camera_model["quality"]["confidence"],
            "camera_extrinsics": 0.0,
            "straight_step_unit": confidence,
            "turn_radius_estimates": 0.0,
            "world_model": confidence,
            "overall": confidence,
        },
    }


def compact_backtest_summary(backtest: dict[str, Any] | None) -> dict[str, Any] | None:
    if backtest is None:
        return None
    return {
        "method": backtest["method"],
        "pair_count": backtest["pair_count"],
        "valid_pair_count": backtest["valid_pair_count"],
        "overall_score": backtest["overall_score"],
        "score_basis": backtest["score_basis"],
        "median_error_px": backtest["median_error_px"],
        "p95_error_px": backtest["p95_error_px"],
        "mean_error_px": backtest["mean_error_px"],
        "inlier_ratio_under_threshold": backtest["inlier_ratio_under_threshold"],
        "full_frame": backtest["full_frame"],
        "motion_group_regions": backtest["motion_group_regions"],
        "nearer_candidate_regions": backtest["nearer_candidate_regions"],
        "threshold_px": backtest["threshold_px"],
        "holdout_stride": backtest["holdout_stride"],
        "region_margin_px": backtest["region_margin_px"],
        "pairs": [
            {
                "pair": pair["pair"],
                "status": pair["status"],
                "match_count": pair["match_count"],
                "train_count": pair["train_count"],
                "holdout_count": pair["holdout_count"],
                "predicted_count": pair.get("predicted_count", 0),
                "hypothesis_count": pair["hypothesis_count"],
                "score": pair["score"],
                "score_basis": pair.get("score_basis"),
                "median_error_px": pair["median_error_px"],
                "p95_error_px": pair["p95_error_px"],
                "mean_error_px": pair["mean_error_px"],
                "inlier_ratio_under_threshold": pair["inlier_ratio_under_threshold"],
                "full_frame": pair.get("full_frame"),
                "motion_group_regions": pair.get("motion_group_regions"),
                "nearer_candidate_regions": pair.get("nearer_candidate_regions"),
                "output_files": pair["output_files"],
            }
            for pair in backtest["pairs"]
        ],
        "output_files": backtest["output_files"],
    }


def write_html_report(
    html_path: Path,
    manifest: dict[str, Any],
    image_paths: list[Path],
    pairs: list[dict[str, Any]],
    calibration: dict[str, Any],
    backtest: dict[str, Any] | None,
) -> None:
    html_path.parent.mkdir(parents=True, exist_ok=True)

    def rel(path: str | Path) -> str:
        return html.escape(os.path.relpath(Path(path), html_path.parent))

    frame_figures = "\n".join(
        f"""
        <figure>
          <img src="{rel(path)}" alt="Frame {index}">
          <figcaption>{html.escape(Path(path).name)}</figcaption>
        </figure>
        """
        for index, path in enumerate(image_paths)
    )
    pair_cards = "\n".join(pair_card(index, pair, rel) for index, pair in enumerate(pairs))
    backtest_cards = "" if backtest is None else "\n".join(backtest_pair_card(pair, rel) for pair in backtest["pairs"])
    confidence = calibration["confidence"]["overall"]
    backtest_score = None if backtest is None else backtest["overall_score"]
    backtest_label = "n/a" if backtest_score is None else f"{backtest_score:.2f}"
    near_count = calibration["scene_depth"]["nearer_candidate_count"]

    html_path.write_text(f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PiRacer Visual Depth Calibration</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #111;
      --panel: #1c1c1c;
      --panel-2: #242424;
      --line: #3b3b3b;
      --text: #f2f2f2;
      --muted: #bdbdbd;
      --accent: #91d0ff;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
    }}
    main {{
      width: min(1500px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 24px 0 42px;
    }}
    header {{
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 18px;
      align-items: end;
      padding-bottom: 16px;
      border-bottom: 1px solid var(--line);
    }}
    h1 {{ margin: 0 0 8px; font-size: 24px; letter-spacing: 0; }}
    h2 {{ margin: 30px 0 12px; font-size: 18px; letter-spacing: 0; }}
    h3 {{ margin: 0 0 10px; font-size: 15px; letter-spacing: 0; }}
    p {{ margin: 0; color: var(--muted); line-height: 1.45; }}
    code {{ color: #dbeeff; overflow-wrap: anywhere; }}
    .metrics {{
      display: grid;
      grid-template-columns: repeat(4, minmax(110px, 1fr));
      gap: 10px;
    }}
    .metric {{
      background: var(--panel);
      border: 1px solid var(--line);
      padding: 10px 12px;
    }}
    .metric strong {{ display: block; font-size: 20px; line-height: 1.1; }}
    .metric span {{ display: block; margin-top: 4px; color: var(--muted); font-size: 12px; }}
    .frames {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 10px;
    }}
    .pairs {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 18px;
    }}
    figure, article {{
      margin: 0;
      background: var(--panel);
      border: 1px solid var(--line);
    }}
    figcaption, .body {{ padding: 10px 12px; }}
    figcaption {{ color: var(--muted); border-top: 1px solid var(--line); font-size: 13px; }}
    img {{ display: block; width: 100%; height: auto; background: #050505; }}
    table {{ width: 100%; border-collapse: collapse; color: var(--muted); font-size: 13px; }}
    th, td {{ padding: 7px 6px; border-top: 1px solid var(--line); text-align: left; vertical-align: top; }}
    th {{ color: var(--text); font-weight: 600; }}
    .note {{
      background: var(--panel);
      border: 1px solid var(--line);
      padding: 12px;
      margin-top: 16px;
    }}
    @media (max-width: 920px) {{
      header, .pairs {{ grid-template-columns: 1fr; }}
      .metrics {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>PiRacer Visual Depth Calibration</h1>
        <p>Run <code>{html.escape(str(manifest.get("run_id", "unknown")))}</code>. This is feature-motion depth calibration, not semantic object detection.</p>
      </div>
      <div class="metrics">
        <div class="metric"><strong>{len(image_paths)}</strong><span>frames</span></div>
        <div class="metric"><strong>{len(pairs)}</strong><span>motion pairs</span></div>
        <div class="metric"><strong>{near_count}</strong><span>nearer candidates</span></div>
        <div class="metric"><strong>{backtest_label}</strong><span>backtest score</span></div>
        <div class="metric"><strong>{confidence:.2f}</strong><span>confidence</span></div>
      </div>
    </header>

    <h2>Raw Frames</h2>
    <section class="frames">
      {frame_figures}
    </section>

    <h2>Scene Depth Motion Groups</h2>
    <section class="pairs">
      {pair_cards}
    </section>

    <h2>Held-Out Feature Backtest</h2>
    <section class="pairs">
      {backtest_cards or '<article><div class="body"><p>Backtest was disabled for this run.</p></div></article>'}
    </section>

    <section class="note">
      <p>
        Interpretation: groups with scale above threshold are treated as nearer/depth-edge candidates.
        The nominal step is only a coordinate convention; this run does not estimate physical meters.
        The backtest score is computed from held-out feature matches from the same capture pair, so it is a fit check,
        not an independent validation run.
      </p>
    </section>
  </main>
</body>
</html>
""", encoding="utf-8")


def pair_card(index: int, pair: dict[str, Any], rel) -> str:
    overlay = pair["output_files"]["scene_motion"]
    rows = "\n".join(
        f"""
        <tr>
          <td>{group["group_id"]}</td>
          <td>{html.escape(group["role"])}</td>
          <td>{html.escape(group["depth_hint"])}</td>
          <td>{group["match_count"]}</td>
          <td>{html.escape(str(group["source_bbox"]))}</td>
          <td>{group["scale"] if group["scale"] is None else f'{group["scale"]:.3f}'}</td>
        </tr>
        """
        for group in pair["groups"]
    ) or '<tr><td colspan="6">No coherent motion groups.</td></tr>'
    return f"""
      <article>
        <img src="{rel(overlay)}" alt="Scene motion pair {index}">
        <div class="body">
          <h3>Pair {index}: {Path(pair["image_a"]).name} -> {Path(pair["image_b"]).name}</h3>
          <p>{pair["match_count"]} matches, {pair["grouped_match_count"]} grouped, grouped ratio {pair["grouped_ratio"]:.2f}</p>
          <table>
            <thead>
              <tr><th>ID</th><th>Role</th><th>Depth hint</th><th>Matches</th><th>Source bbox</th><th>Scale</th></tr>
            </thead>
            <tbody>{rows}</tbody>
          </table>
        </div>
      </article>
    """


def backtest_pair_card(pair: dict[str, Any], rel) -> str:
    overlay = pair.get("output_files", {}).get("candidate_overlay") or pair.get("output_files", {}).get("overlay")
    image = f'<img src="{rel(overlay)}" alt="Held-out prediction overlay">' if overlay else ""
    if pair["status"] != "ok":
        rows = f'<tr><td colspan="6">{html.escape(pair["status"])}</td></tr>'
    else:
        full = pair.get("full_frame", {})
        candidate = pair.get("motion_group_regions", {})
        nearer = pair.get("nearer_candidate_regions", {})
        rows = f"""
          <tr>
            <td>selected: {html.escape(str(pair.get("score_basis", "unknown")))}</td>
            <td>{pair["score"]:.3f}</td>
            <td>{pair["median_error_px"] if pair["median_error_px"] is None else f'{pair["median_error_px"]:.2f}'}</td>
            <td>{pair["p95_error_px"] if pair["p95_error_px"] is None else f'{pair["p95_error_px"]:.2f}'}</td>
            <td>{pair["inlier_ratio_under_threshold"]:.2f}</td>
            <td>{candidate.get("predicted_count", 0)}/{pair["holdout_count"]}</td>
          </tr>
          <tr>
            <td>full frame</td>
            <td>{full.get("score", 0.0):.3f}</td>
            <td>{full.get("median_error_px") if full.get("median_error_px") is None else f'{full.get("median_error_px"):.2f}'}</td>
            <td>{full.get("p95_error_px") if full.get("p95_error_px") is None else f'{full.get("p95_error_px"):.2f}'}</td>
            <td>{full.get("inlier_ratio_under_threshold", 0.0):.2f}</td>
            <td>{full.get("predicted_count", 0)}/{pair["holdout_count"]}</td>
          </tr>
          <tr>
            <td>nearer regions</td>
            <td>{nearer.get("score", 0.0):.3f}</td>
            <td>{nearer.get("median_error_px") if nearer.get("median_error_px") is None else f'{nearer.get("median_error_px"):.2f}'}</td>
            <td>{nearer.get("p95_error_px") if nearer.get("p95_error_px") is None else f'{nearer.get("p95_error_px"):.2f}'}</td>
            <td>{nearer.get("inlier_ratio_under_threshold", 0.0):.2f}</td>
            <td>{nearer.get("predicted_count", 0)}/{pair["holdout_count"]}</td>
          </tr>
        """
    pair_label = f"{pair['pair'][0]} -> {pair['pair'][1]}"
    return f"""
      <article>
        {image}
        <div class="body">
          <h3>Backtest Pair {html.escape(pair_label)}</h3>
          <p>
            Train {pair["train_count"]} matches, hold out {pair["holdout_count"]};
            threshold {pair["threshold_px"]} px.
          </p>
          <table>
            <thead>
              <tr><th>Region</th><th>Score</th><th>Median px</th><th>P95 px</th><th>Inlier ratio</th><th>Predicted</th></tr>
            </thead>
            <tbody>{rows}</tbody>
          </table>
        </div>
      </article>
    """


def add_capture_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--base-url", default="http://127.0.0.1:8887")
    parser.add_argument("--frame-endpoint", default="/frame.jpg")
    parser.add_argument("--throttle", type=float, default=0.16)
    parser.add_argument("--steering", type=float, default=0.0)
    parser.add_argument("--duration", type=float, default=0.25)
    parser.add_argument("--settle", type=float, default=0.35)
    parser.add_argument("--robot-id", default="piracer")
    parser.add_argument("--environment-id", default="default-room")
    parser.add_argument("--camera-mount", default="piracer_fixed_front")


def add_capture_args(parser: argparse.ArgumentParser) -> None:
    add_capture_common_args(parser)
    parser.add_argument("--pulses", type=int, default=5)


def add_analysis_args(parser: argparse.ArgumentParser, *, include_identity: bool = True) -> None:
    parser.add_argument("--max-features", type=int, default=240)
    parser.add_argument("--min-distance", type=int, default=5)
    parser.add_argument("--patch-radius", type=int, default=4)
    parser.add_argument("--search-radius", type=int, default=90)
    parser.add_argument("--min-score", type=float, default=0.70)
    parser.add_argument("--max-groups", type=int, default=6)
    parser.add_argument("--min-group-size", type=int, default=8)
    parser.add_argument("--residual-threshold", type=float, default=7.0)
    parser.add_argument("--near-scale-threshold", type=float, default=1.06)
    parser.add_argument("--hfov-deg", type=float, default=62.2)
    parser.add_argument("--vfov-deg", type=float, default=48.8)
    parser.add_argument("--no-backtest", action="store_true",
                        help="Skip held-out feature prediction scoring.")
    parser.add_argument("--backtest-holdout-stride", type=int, default=4,
                        help="Hold out every Nth feature match for prediction scoring.")
    parser.add_argument("--backtest-residual-threshold", type=float, default=7.0,
                        help="Pixel residual threshold counted as a successful held-out prediction.")
    parser.add_argument("--backtest-min-train-matches", type=int, default=24)
    parser.add_argument("--backtest-min-holdout-matches", type=int, default=8)
    parser.add_argument("--backtest-min-candidate-records", type=int, default=6,
                        help="Minimum held-out matches inside motion-group regions before using candidate-region score.")
    parser.add_argument("--backtest-region-margin", type=float, default=18.0,
                        help="Pixel margin around motion-group boxes used to classify held-out matches as candidate-region matches.")
    if include_identity:
        parser.add_argument("--robot-id", default="piracer")
        parser.add_argument("--environment-id", default="default-room")


def command_from_args(args: argparse.Namespace) -> RobotCommand:
    return RobotCommand(
        throttle=args.throttle,
        steering=args.steering,
        duration_s=args.duration,
        settle_s=args.settle,
    )


def cmd_collect_straight(args: argparse.Namespace) -> int:
    command = command_from_args(args)
    out_dir = Path(args.out_dir or Path("lab") / "runs" / "calibration" / now_id("straight"))
    run = collect_pulse_run(
        run_type="straight_step",
        base_url=args.base_url,
        frame_endpoint=args.frame_endpoint,
        pulses=args.pulses,
        command=command,
        out_dir=out_dir,
        camera_mount=args.camera_mount,
        robot_id=args.robot_id,
        environment_id=args.environment_id,
    )
    print(f"out_dir: {out_dir}")
    print(f"frames: {len(run.frames)}")
    print(f"manifest: {out_dir / 'capture_run.json'}")
    return 0


def cmd_collect_turn(args: argparse.Namespace) -> int:
    command = command_from_args(args)
    out_dir = Path(args.out_dir or Path("lab") / "runs" / "calibration" / now_id("turn"))
    run = collect_pulse_run(
        run_type="turn_radius",
        base_url=args.base_url,
        frame_endpoint=args.frame_endpoint,
        pulses=args.pulses,
        command=command,
        out_dir=out_dir,
        camera_mount=args.camera_mount,
        robot_id=args.robot_id,
        environment_id=args.environment_id,
    )
    print(f"out_dir: {out_dir}")
    print(f"frames: {len(run.frames)}")
    print(f"manifest: {out_dir / 'capture_run.json'}")
    return 0


def cmd_collect_reciprocal(args: argparse.Namespace) -> int:
    forward_command = command_from_args(args)
    reverse_throttle = args.reverse_throttle
    if reverse_throttle is None:
        reverse_throttle = -abs(forward_command.throttle)
    reverse_command = RobotCommand(
        throttle=float(reverse_throttle),
        steering=float(args.reverse_steering),
        duration_s=float(args.reverse_duration if args.reverse_duration is not None else args.duration),
        settle_s=float(args.reverse_settle if args.reverse_settle is not None else args.settle),
    )
    out_dir = Path(args.out_dir or Path("lab") / "runs" / "calibration" / now_id("reciprocal"))
    run = collect_reciprocal_run(
        base_url=args.base_url,
        frame_endpoint=args.frame_endpoint,
        cycles=args.cycles,
        forward_command=forward_command,
        reverse_command=reverse_command,
        out_dir=out_dir,
        camera_mount=args.camera_mount,
        robot_id=args.robot_id,
        environment_id=args.environment_id,
    )
    print(f"out_dir: {out_dir}")
    print(f"frames: {len(run.frames)}")
    print(f"manifest: {out_dir / 'capture_run.json'}")
    print(f"forward: {command_dict(forward_command)}")
    print(f"reverse: {command_dict(reverse_command)}")
    return 0


def cmd_analyze_run(args: argparse.Namespace) -> int:
    manifest, run_dir = load_capture_run(Path(args.capture_run))
    command = command_from_manifest_or_args(manifest, args)
    out_dir = Path(args.out_dir or run_dir / "visual_depth")
    result = analyze_depth_run(
        manifest=manifest,
        run_dir=run_dir,
        image_paths=image_paths_from_run(manifest, run_dir),
        out_dir=out_dir,
        command=command,
        args=args,
    )
    print_analysis_result(result)
    return 0


def cmd_analyze_reciprocal(args: argparse.Namespace) -> int:
    manifest, run_dir = load_capture_run(Path(args.capture_run))
    out_dir = Path(args.out_dir or run_dir / "reciprocal_drift")
    result = analyze_reciprocal_drift(
        manifest=manifest,
        run_dir=run_dir,
        image_paths=image_paths_from_run(manifest, run_dir),
        out_dir=out_dir,
        args=args,
    )
    print(f"out_dir: {out_dir}")
    print(f"summary: {result['output_files']['summary']}")
    print(f"html: {result['output_files']['html']}")
    print(f"confidence: {result['overall_confidence']:.3f}")
    for name, summary in result["summaries"].items():
        print(
            f"{name}: valid={summary['valid_count']}/{summary['count']} "
            f"median_px={summary['median_drift_px']} "
            f"final_px={summary['final_drift_px']} "
            f"pass={summary['pass_ratio_under_threshold']:.3f}"
        )
    return 0


def cmd_analyze_images(args: argparse.Namespace) -> int:
    image_paths = image_paths_from_args(args)
    if len(image_paths) < 2:
        raise SystemExit("need at least two images")
    command = command_from_args(args)
    manifest = synthetic_capture_run(
        image_paths,
        run_type="straight_step",
        command=command,
        robot_id=args.robot_id,
        environment_id=args.environment_id,
    )
    out_dir = Path(args.out_dir or Path("lab") / "runs" / "calibration" / now_id("image-depth"))
    result = analyze_depth_run(
        manifest=manifest,
        run_dir=Path("."),
        image_paths=image_paths,
        out_dir=out_dir,
        command=command,
        args=args,
    )
    print_analysis_result(result)
    return 0


def cmd_run_full(args: argparse.Namespace) -> int:
    command = command_from_args(args)
    out_dir = Path(args.out_dir or Path("lab") / "runs" / "calibration" / now_id("full"))
    capture_dir = out_dir / "capture"
    analysis_dir = out_dir / "visual_depth"
    run = collect_pulse_run(
        run_type="straight_step",
        base_url=args.base_url,
        frame_endpoint=args.frame_endpoint,
        pulses=args.pulses,
        command=command,
        out_dir=capture_dir,
        camera_mount=args.camera_mount,
        robot_id=args.robot_id,
        environment_id=args.environment_id,
    )
    manifest = asdict(run)
    result = analyze_depth_run(
        manifest=manifest,
        run_dir=capture_dir,
        image_paths=image_paths_from_run(manifest, capture_dir),
        out_dir=analysis_dir,
        command=command,
        args=args,
    )
    print(f"capture_dir: {capture_dir}")
    print_analysis_result(result)
    return 0


def command_from_manifest_or_args(manifest: dict[str, Any], args: argparse.Namespace) -> RobotCommand:
    for frame in manifest.get("frames", []):
        command = frame.get("command_before_frame")
        if command:
            return RobotCommand(
                throttle=float(command["throttle"]),
                steering=float(command["steering"]),
                duration_s=float(command["duration_s"]),
                settle_s=float(command["settle_s"]),
            )
    return command_from_args(args)


def print_analysis_result(result: dict[str, Any]) -> None:
    print(f"out_dir: {result['out_dir']}")
    print(f"pairs: {result['pair_count']}")
    print(f"scene_depth_summary: {result['scene_depth_summary']}")
    print(f"calibration: {result['calibration']}")
    print(f"html: {result['html']}")
    print(f"confidence: {result['calibration_bundle']['confidence']['overall']:.3f}")
    if result.get("backtest") is not None:
        backtest = result["backtest"]
        print(
            f"backtest: score={backtest['overall_score']:.3f} "
            f"median_px={backtest['median_error_px']} "
            f"p95_px={backtest['p95_error_px']} "
            f"inliers={backtest['inlier_ratio_under_threshold']:.3f}"
        )
    for index, pair in enumerate(result["pairs"]):
        near = sum(1 for group in pair["groups"] if group["depth_hint"] == "nearer_candidate")
        print(
            f"pair {index}: matches={pair['match_count']} "
            f"grouped={pair['grouped_match_count']} groups={len(pair['groups'])} nearer={near}"
        )


def reciprocal_drift_record(
    image_paths: list[Path],
    source_index: int,
    target_index: int,
    *,
    phase: str,
    comparison: str,
    out_dir: Path,
    args: argparse.Namespace,
) -> dict[str, Any]:
    image_a_path = image_paths[source_index]
    image_b_path = image_paths[target_index]
    width, height = Image.open(image_a_path).size
    bbox = [0, 0, width - 1, height - 1]
    pair_dir = out_dir / f"{comparison}_{phase}_{source_index:03d}_{target_index:03d}"
    result = track_features(
        image_a_path,
        image_b_path,
        bbox,
        pair_dir,
        max_features=args.max_features,
        min_distance=args.min_distance,
        patch_radius=args.patch_radius,
        search_radius=args.search_radius,
        min_score=args.min_score,
    )
    drift_px = None
    if result.median_dx_px is not None and result.median_dy_px is not None:
        drift_px = float(math.hypot(result.median_dx_px, result.median_dy_px))
    scale_error = None if result.scale is None else abs(float(result.scale) - 1.0)
    return {
        "phase": phase,
        "comparison": comparison,
        "source_index": source_index,
        "target_index": target_index,
        "image_a": str(image_a_path),
        "image_b": str(image_b_path),
        "keypoint_count": result.keypoint_count,
        "match_count": result.match_count,
        "inlier_count": result.inlier_count,
        "median_dx_px": result.median_dx_px,
        "median_dy_px": result.median_dy_px,
        "drift_px": drift_px,
        "scale": result.scale,
        "scale_error": scale_error,
        "center_shift_px": result.center_shift_px,
        "output_files": result.output_files,
    }


def summarize_drift_records(records: list[dict[str, Any]], threshold_px: float) -> dict[str, Any]:
    valid = [record for record in records if record["drift_px"] is not None]
    if not valid:
        return {
            "count": len(records),
            "valid_count": 0,
            "median_drift_px": None,
            "mean_drift_px": None,
            "max_drift_px": None,
            "final_drift_px": None,
            "pass_ratio_under_threshold": 0.0,
            "confidence": 0.0,
        }
    drifts = np.asarray([record["drift_px"] for record in valid], dtype=np.float64)
    final_record = sorted(valid, key=lambda item: item["target_index"])[-1]
    pass_ratio = float((drifts <= threshold_px).mean())
    median_drift = float(np.median(drifts))
    median_score = max(0.0, min(1.0, 1.0 - median_drift / max(threshold_px * 2.0, 1e-6)))
    confidence = 0.60 * pass_ratio + 0.40 * median_score
    return {
        "count": len(records),
        "valid_count": len(valid),
        "median_drift_px": median_drift,
        "mean_drift_px": float(np.mean(drifts)),
        "max_drift_px": float(np.max(drifts)),
        "final_drift_px": final_record["drift_px"],
        "pass_ratio_under_threshold": pass_ratio,
        "confidence": float(confidence),
    }


def analyze_reciprocal_drift(
    *,
    manifest: dict[str, Any],
    run_dir: Path,
    image_paths: list[Path],
    out_dir: Path,
    args: argparse.Namespace,
) -> dict[str, Any]:
    if len(image_paths) < 3:
        raise SystemExit("need at least three images for reciprocal drift analysis")
    out_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []

    for source_index in range(0, len(image_paths) - 2, 2):
        records.append(reciprocal_drift_record(
            image_paths,
            source_index,
            source_index + 2,
            phase="home",
            comparison="incremental",
            out_dir=out_dir,
            args=args,
        ))
    for target_index in range(2, len(image_paths), 2):
        records.append(reciprocal_drift_record(
            image_paths,
            0,
            target_index,
            phase="home",
            comparison="cumulative",
            out_dir=out_dir,
            args=args,
        ))
    for source_index in range(1, len(image_paths) - 2, 2):
        records.append(reciprocal_drift_record(
            image_paths,
            source_index,
            source_index + 2,
            phase="forward",
            comparison="incremental",
            out_dir=out_dir,
            args=args,
        ))
    for target_index in range(3, len(image_paths), 2):
        records.append(reciprocal_drift_record(
            image_paths,
            1,
            target_index,
            phase="forward",
            comparison="cumulative",
            out_dir=out_dir,
            args=args,
        ))

    summaries = {
        "home_incremental": summarize_drift_records(
            [record for record in records if record["phase"] == "home" and record["comparison"] == "incremental"],
            args.drift_threshold_px,
        ),
        "home_cumulative": summarize_drift_records(
            [record for record in records if record["phase"] == "home" and record["comparison"] == "cumulative"],
            args.drift_threshold_px,
        ),
        "forward_incremental": summarize_drift_records(
            [record for record in records if record["phase"] == "forward" and record["comparison"] == "incremental"],
            args.drift_threshold_px,
        ),
        "forward_cumulative": summarize_drift_records(
            [record for record in records if record["phase"] == "forward" and record["comparison"] == "cumulative"],
            args.drift_threshold_px,
        ),
    }
    overall_confidence = (
        0.55 * summaries["home_cumulative"]["confidence"]
        + 0.25 * summaries["home_incremental"]["confidence"]
        + 0.20 * summaries["forward_cumulative"]["confidence"]
    )
    result = {
        "method": "reciprocal_forward_reverse_drift",
        "description": (
            "Compare every-other frame in an alternating forward/reverse pulse run. "
            "Even frames should return to the home pose; odd frames should return to the one-step-forward pose."
        ),
        "source_run_dir": str(run_dir),
        "frame_count": len(image_paths),
        "threshold_px": args.drift_threshold_px,
        "summaries": summaries,
        "overall_confidence": float(overall_confidence),
        "records": records,
        "output_files": {
            "summary": str(out_dir / "reciprocal_drift.json"),
            "html": str(out_dir / "index.html"),
        },
    }
    write_json(out_dir / "reciprocal_drift.json", result)
    write_reciprocal_html(out_dir / "index.html", manifest, image_paths, result)
    return result


def write_reciprocal_html(
    html_path: Path,
    manifest: dict[str, Any],
    image_paths: list[Path],
    result: dict[str, Any],
) -> None:
    html_path.parent.mkdir(parents=True, exist_ok=True)

    def rel(path: str | Path) -> str:
        return html.escape(os.path.relpath(Path(path), html_path.parent))

    def fmt(value: Any, digits: int = 2) -> str:
        if value is None:
            return "n/a"
        if isinstance(value, float):
            return f"{value:.{digits}f}"
        return html.escape(str(value))

    frame_figures = "\n".join(
        f"""
        <figure>
          <img src="{rel(path)}" alt="Frame {index}">
          <figcaption>{index}: {html.escape(Path(path).name)}</figcaption>
        </figure>
        """
        for index, path in enumerate(image_paths)
    )
    summary_rows = "\n".join(
        f"""
        <tr>
          <td>{html.escape(name)}</td>
          <td>{summary["valid_count"]}/{summary["count"]}</td>
          <td>{fmt(summary["median_drift_px"])}</td>
          <td>{fmt(summary["final_drift_px"])}</td>
          <td>{fmt(summary["max_drift_px"])}</td>
          <td>{fmt(summary["pass_ratio_under_threshold"])}</td>
          <td>{fmt(summary["confidence"])}</td>
        </tr>
        """
        for name, summary in result["summaries"].items()
    )
    record_cards = "\n".join(reciprocal_record_card(record, rel, fmt) for record in result["records"])
    threshold = result["threshold_px"]
    html_path.write_text(f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PiRacer Reciprocal Drift</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #111;
      --panel: #1c1c1c;
      --line: #3a3a3a;
      --text: #f2f2f2;
      --muted: #bdbdbd;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
    }}
    main {{
      width: min(1500px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 24px 0 42px;
    }}
    header {{
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 18px;
      align-items: end;
      padding-bottom: 16px;
      border-bottom: 1px solid var(--line);
    }}
    h1 {{ margin: 0 0 8px; font-size: 24px; letter-spacing: 0; }}
    h2 {{ margin: 30px 0 12px; font-size: 18px; letter-spacing: 0; }}
    h3 {{ margin: 0 0 10px; font-size: 15px; letter-spacing: 0; }}
    p {{ margin: 0; color: var(--muted); line-height: 1.45; }}
    code {{ color: #dbeeff; overflow-wrap: anywhere; }}
    .metrics {{
      display: grid;
      grid-template-columns: repeat(3, minmax(120px, 1fr));
      gap: 10px;
    }}
    .metric, figure, article {{
      background: var(--panel);
      border: 1px solid var(--line);
    }}
    .metric {{ padding: 10px 12px; }}
    .metric strong {{ display: block; font-size: 20px; line-height: 1.1; }}
    .metric span {{ display: block; margin-top: 4px; color: var(--muted); font-size: 12px; }}
    .frames {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      gap: 10px;
    }}
    .records {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 18px;
    }}
    figure {{ margin: 0; }}
    figcaption, .body {{ padding: 10px 12px; }}
    figcaption {{ color: var(--muted); border-top: 1px solid var(--line); font-size: 13px; }}
    img {{ display: block; width: 100%; height: auto; background: #050505; }}
    table {{ width: 100%; border-collapse: collapse; color: var(--muted); font-size: 13px; }}
    th, td {{ padding: 7px 6px; border-top: 1px solid var(--line); text-align: left; vertical-align: top; }}
    th {{ color: var(--text); font-weight: 600; }}
    .note {{
      background: var(--panel);
      border: 1px solid var(--line);
      padding: 12px;
      margin-top: 16px;
    }}
    @media (max-width: 920px) {{
      header, .records {{ grid-template-columns: 1fr; }}
      .metrics {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>PiRacer Reciprocal Drift</h1>
        <p>Run <code>{html.escape(str(manifest.get("run_id", "unknown")))}</code>. Even frames are home-return checks; odd frames are forward-pose repeat checks.</p>
      </div>
      <div class="metrics">
        <div class="metric"><strong>{len(image_paths)}</strong><span>frames</span></div>
        <div class="metric"><strong>{fmt(result["threshold_px"])}</strong><span>drift threshold px</span></div>
        <div class="metric"><strong>{fmt(result["overall_confidence"])}</strong><span>confidence</span></div>
      </div>
    </header>

    <h2>Summary</h2>
    <table>
      <thead>
        <tr><th>Check</th><th>Valid</th><th>Median drift px</th><th>Final drift px</th><th>Max drift px</th><th>Pass ratio</th><th>Confidence</th></tr>
      </thead>
      <tbody>{summary_rows}</tbody>
    </table>

    <h2>Raw Frames</h2>
    <section class="frames">
      {frame_figures}
    </section>

    <h2>Every-Other Comparisons</h2>
    <section class="records">
      {record_cards}
    </section>

    <section class="note">
      <p>
        Interpretation: a good reciprocal step has low home cumulative drift across 0→2→4… and low forward cumulative drift across 1→3→5….
        This checks repeatability of the command pair. It does not prove metric distance, but it directly tests whether forward plus reverse returns to the same visual pose.
        The default pass threshold is {fmt(threshold)} px of median feature shift.
      </p>
    </section>
  </main>
</body>
</html>
""", encoding="utf-8")


def reciprocal_record_card(record: dict[str, Any], rel, fmt) -> str:
    overlay = record["output_files"].get("matches")
    image = f'<img src="{rel(overlay)}" alt="Feature matches">' if overlay else ""
    title = (
        f"{record['comparison']} {record['phase']}: "
        f"{record['source_index']} -> {record['target_index']}"
    )
    return f"""
      <article>
        {image}
        <div class="body">
          <h3>{html.escape(title)}</h3>
          <table>
            <tbody>
              <tr><th>Matches</th><td>{record["match_count"]}</td></tr>
              <tr><th>Inliers</th><td>{record["inlier_count"]}</td></tr>
              <tr><th>Median dx/dy</th><td>{fmt(record["median_dx_px"])}, {fmt(record["median_dy_px"])}</td></tr>
              <tr><th>Drift px</th><td>{fmt(record["drift_px"])}</td></tr>
              <tr><th>Scale</th><td>{fmt(record["scale"], 4)}</td></tr>
            </tbody>
          </table>
        </div>
      </article>
    """


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="PiRacer visual depth calibration entrypoint. Runs on the Pi or locally against saved frames."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    collect_straight = subparsers.add_parser("collect-straight", help="Capture a straight-pulse calibration run.")
    add_capture_args(collect_straight)
    collect_straight.add_argument("--out-dir", default=None)
    collect_straight.set_defaults(func=cmd_collect_straight)

    collect_turn = subparsers.add_parser("collect-turn", help="Capture one fixed-steering turn run.")
    add_capture_args(collect_turn)
    collect_turn.add_argument("--out-dir", default=None)
    collect_turn.set_defaults(func=cmd_collect_turn)

    collect_reciprocal = subparsers.add_parser(
        "collect-reciprocal",
        help="Capture alternating forward/reverse pulses for drift testing.",
    )
    add_capture_common_args(collect_reciprocal)
    collect_reciprocal.add_argument("--cycles", type=int, default=4)
    collect_reciprocal.add_argument("--reverse-throttle", type=float, default=None)
    collect_reciprocal.add_argument("--reverse-steering", type=float, default=0.0)
    collect_reciprocal.add_argument("--reverse-duration", type=float, default=None)
    collect_reciprocal.add_argument("--reverse-settle", type=float, default=None)
    collect_reciprocal.add_argument("--out-dir", default=None)
    collect_reciprocal.set_defaults(func=cmd_collect_reciprocal)

    analyze_run = subparsers.add_parser("analyze-run", help="Analyze a saved capture_run.json.")
    analyze_run.add_argument("capture_run")
    analyze_run.add_argument("--out-dir", default=None)
    analyze_run.add_argument("--throttle", type=float, default=0.16)
    analyze_run.add_argument("--steering", type=float, default=0.0)
    analyze_run.add_argument("--duration", type=float, default=0.25)
    analyze_run.add_argument("--settle", type=float, default=0.35)
    add_analysis_args(analyze_run)
    analyze_run.set_defaults(func=cmd_analyze_run)

    analyze_reciprocal = subparsers.add_parser(
        "analyze-reciprocal",
        help="Analyze every-other-frame drift from a reciprocal capture_run.json.",
    )
    analyze_reciprocal.add_argument("capture_run")
    analyze_reciprocal.add_argument("--out-dir", default=None)
    add_analysis_args(analyze_reciprocal)
    analyze_reciprocal.add_argument("--drift-threshold-px", type=float, default=12.0)
    analyze_reciprocal.set_defaults(func=cmd_analyze_reciprocal)

    analyze_images = subparsers.add_parser("analyze-images", help="Analyze an existing image burst.")
    analyze_images.add_argument("images", nargs="*")
    analyze_images.add_argument("--image-dir", default=None)
    analyze_images.add_argument("--pattern", default="*.jpg")
    analyze_images.add_argument("--out-dir", default=None)
    analyze_images.add_argument("--throttle", type=float, default=0.16)
    analyze_images.add_argument("--steering", type=float, default=0.0)
    analyze_images.add_argument("--duration", type=float, default=0.25)
    analyze_images.add_argument("--settle", type=float, default=0.35)
    add_analysis_args(analyze_images)
    analyze_images.set_defaults(func=cmd_analyze_images)

    run_full = subparsers.add_parser("run-full", help="Capture a straight run and analyze visual depth groups.")
    add_capture_args(run_full)
    add_analysis_args(run_full, include_identity=False)
    run_full.add_argument("--out-dir", default=None)
    run_full.set_defaults(func=cmd_run_full)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
