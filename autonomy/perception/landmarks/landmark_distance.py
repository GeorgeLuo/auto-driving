from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from autonomy.perception.features import FeatureMatch, FeatureTrackingResult, track_features
from autonomy.perception.motion import MotionGroup, analyze_scene_motion


@dataclass
class LandmarkSelection:
    group_id: int
    score: float
    source_bbox: list[int]
    target_bbox: list[int]
    scale: float
    match_count: int
    median_residual_px: float
    distance_before_first_step: float
    distance_after_first_step: float


@dataclass
class LandmarkStepEstimate:
    step: float
    image: str
    bbox: list[int]
    pair_scale: float | None
    cumulative_scale: float | None
    distance_from_start_steps: float | None
    distance_remaining_steps: float | None
    inliers: int
    matches: int
    center_shift_px: list[float] | None
    matches_image: str | None


@dataclass
class LandmarkDistanceResult:
    images: list[str]
    steps: list[float]
    landmark: LandmarkSelection | None
    estimates: list[LandmarkStepEstimate]
    output_files: dict[str, str]


def estimate_landmark_distance(
    image_paths: list[str | Path],
    out_dir: str | Path,
    *,
    steps: list[float] | None = None,
    scene_max_features: int = 600,
    scene_search_radius: int = 260,
    scene_min_score: float = 0.66,
    scene_min_group_size: int = 10,
    track_max_features: int = 140,
    track_search_radius: int = 260,
    track_min_score: float = 0.66,
    bbox_padding: int = 24,
) -> LandmarkDistanceResult:
    if len(image_paths) < 2:
        raise ValueError("at least two images are required")

    image_paths = [Path(path) for path in image_paths]
    if steps is None:
        steps = [float(index) for index in range(len(image_paths))]
    if len(steps) != len(image_paths):
        raise ValueError("steps must have the same length as image_paths")

    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    scene = analyze_scene_motion(
        image_paths[0],
        image_paths[1],
        out_path / "scene_00_01",
        max_features=scene_max_features,
        min_distance=7,
        search_radius=scene_search_radius,
        min_score=scene_min_score,
        min_group_size=scene_min_group_size,
    )
    first_step_distance = max(steps[1] - steps[0], 1e-6)
    landmark = select_landmark(scene.groups, Image.open(image_paths[0]).size, first_step_distance)

    estimates: list[LandmarkStepEstimate] = [
        LandmarkStepEstimate(
            step=float(steps[0]),
            image=str(image_paths[0]),
            bbox=landmark.source_bbox if landmark else [],
            pair_scale=None,
            cumulative_scale=1.0 if landmark else None,
            distance_from_start_steps=0.0 if landmark else None,
            distance_remaining_steps=landmark.distance_before_first_step if landmark else None,
            inliers=0,
            matches=0,
            center_shift_px=None,
            matches_image=None,
        )
    ]

    if landmark is None:
        return write_result(image_paths, steps, None, estimates, out_path)

    current_bbox = landmark.source_bbox
    cumulative_scale = 1.0
    previous_image = image_paths[0]
    for index, image_path in enumerate(image_paths[1:], start=1):
        track = track_features(
            previous_image,
            image_path,
            current_bbox,
            out_path / f"track_{index - 1:02d}_{index:02d}",
            max_features=track_max_features,
            search_radius=track_search_radius,
            min_score=track_min_score,
        )
        if track.scale is not None and track.scale > 0:
            cumulative_scale *= track.scale

        distance_from_start = float(steps[index] - steps[0])
        distance_remaining = distance_remaining_from_scale(cumulative_scale, distance_from_start)
        estimates.append(
            LandmarkStepEstimate(
                step=float(steps[index]),
                image=str(image_path),
                bbox=current_bbox,
                pair_scale=track.scale,
                cumulative_scale=cumulative_scale,
                distance_from_start_steps=distance_from_start,
                distance_remaining_steps=distance_remaining,
                inliers=track.inlier_count,
                matches=track.match_count,
                center_shift_px=track.center_shift_px,
                matches_image=track.output_files["matches"],
            )
        )
        next_bbox = bbox_from_inlier_targets(track.matches, Image.open(image_path).size, padding=bbox_padding)
        if next_bbox is not None:
            current_bbox = next_bbox
        previous_image = image_path

    return write_result(image_paths, steps, landmark, estimates, out_path)


def select_landmark(
    groups: list[MotionGroup],
    image_size: tuple[int, int],
    first_step_distance: float,
) -> LandmarkSelection | None:
    scored: list[tuple[float, MotionGroup]] = []
    width, height = image_size
    frame_area = width * height
    for group in groups:
        score = landmark_score(group, frame_area, width, height)
        if score is not None:
            scored.append((score, group))

    if not scored:
        return None

    score, group = max(scored, key=lambda item: item[0])
    scale = float(group.scale)
    distance_before = first_step_distance * scale / (scale - 1.0)
    distance_after = first_step_distance / (scale - 1.0)
    return LandmarkSelection(
        group_id=group.group_id,
        score=float(score),
        source_bbox=group.source_bbox,
        target_bbox=group.target_bbox,
        scale=scale,
        match_count=group.match_count,
        median_residual_px=group.median_residual_px,
        distance_before_first_step=float(distance_before),
        distance_after_first_step=float(distance_after),
    )


def landmark_score(
    group: MotionGroup,
    frame_area: int,
    image_width: int,
    image_height: int,
) -> float | None:
    if group.scale is None or group.scale <= 1.03:
        return None
    if group.match_count < 8:
        return None

    x0, y0, x1, y1 = group.source_bbox
    area_ratio = max(1, (x1 - x0 + 1) * (y1 - y0 + 1)) / frame_area
    if area_ratio > 0.85:
        return None

    cx = (x0 + x1) / 2.0
    cy = (y0 + y1) / 2.0
    centrality = 1.0 - min(abs(cx - image_width / 2.0) / (image_width / 2.0), 1.0)
    vertical_presence = 1.0 - min(abs(cy - image_height * 0.55) / (image_height * 0.55), 1.0)
    area_score = min(area_ratio / 0.16, 1.0) if area_ratio < 0.16 else max(0.25, 1.0 - (area_ratio - 0.16))
    match_score = min(group.match_count / 40.0, 1.0)
    expansion_score = min((group.scale - 1.0) / 0.20, 1.5)
    residual_score = 1.0 / (1.0 + group.median_residual_px)
    return float(
        expansion_score
        * (0.35 + 0.65 * match_score)
        * (0.50 + 0.50 * centrality)
        * (0.50 + 0.50 * vertical_presence)
        * area_score
        * residual_score
    )


def distance_remaining_from_scale(cumulative_scale: float | None, distance_from_start: float) -> float | None:
    if cumulative_scale is None or cumulative_scale <= 1.0 or distance_from_start <= 0:
        return None
    return float(distance_from_start / (cumulative_scale - 1.0))


def bbox_from_inlier_targets(
    matches: list[FeatureMatch],
    image_size: tuple[int, int],
    *,
    padding: int,
) -> list[int] | None:
    target = np.array([match.target for match in matches if match.inlier], dtype=np.float64)
    if len(target) < 4:
        return None
    low = np.percentile(target, 10, axis=0)
    high = np.percentile(target, 90, axis=0)
    x0, y0 = np.floor(low).astype(int)
    x1, y1 = np.ceil(high).astype(int)
    width, height = image_size
    return [
        max(0, int(x0 - padding)),
        max(0, int(y0 - padding)),
        min(width - 1, int(x1 + padding)),
        min(height - 1, int(y1 + padding)),
    ]


def write_result(
    image_paths: list[Path],
    steps: list[float],
    landmark: LandmarkSelection | None,
    estimates: list[LandmarkStepEstimate],
    out_path: Path,
) -> LandmarkDistanceResult:
    summary_path = out_path / "summary.json"
    result = LandmarkDistanceResult(
        images=[str(path) for path in image_paths],
        steps=[float(step) for step in steps],
        landmark=landmark,
        estimates=estimates,
        output_files={"summary": str(summary_path)},
    )
    summary_path.write_text(json.dumps(asdict(result), indent=2), encoding="utf-8")
    return result
