from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from autonomy.perception.features.feature_tracking import (
    FeatureMatch,
    detect_keypoints,
    grayscale,
    match_keypoints,
    similarity_scale,
)


@dataclass
class MotionGroup:
    group_id: int
    match_count: int
    source_bbox: list[int]
    target_bbox: list[int]
    center_shift_px: list[float]
    median_motion_px: list[float]
    scale: float | None
    median_residual_px: float
    mean_score: float
    kind_hint: str


@dataclass
class SceneMotionResult:
    image_a: str
    image_b: str
    roi: list[int]
    keypoint_count: int
    match_count: int
    grouped_match_count: int
    ungrouped_match_count: int
    groups: list[MotionGroup]
    output_files: dict[str, str]


def analyze_scene_motion(
    image_a_path: str | Path,
    image_b_path: str | Path,
    out_dir: str | Path,
    *,
    max_features: int = 240,
    min_distance: int = 5,
    patch_radius: int = 4,
    search_radius: int = 90,
    min_score: float = 0.70,
    max_groups: int = 6,
    min_group_size: int = 8,
    residual_threshold: float = 7.0,
    roi: list[int] | None = None,
) -> SceneMotionResult:
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    image_a = Image.open(image_a_path).convert("RGB")
    image_b = Image.open(image_b_path).convert("RGB")
    width, height = image_a.size
    search_roi = clamp_roi(roi or [0, 0, width - 1, height - 1], width, height)

    gray_a = grayscale(image_a)
    gray_b = grayscale(image_b)
    keypoints = detect_keypoints(
        gray_a,
        search_roi,
        max_features=max_features,
        min_distance=min_distance,
        patch_radius=patch_radius,
    )
    matches = match_keypoints(
        gray_a,
        gray_b,
        keypoints,
        patch_radius=patch_radius,
        search_radius=search_radius,
        min_score=min_score,
    )
    groups, grouped_indices = find_motion_groups(
        matches,
        max_groups=max_groups,
        min_group_size=min_group_size,
        residual_threshold=residual_threshold,
    )

    debug_path = out_path / "scene_motion.jpg"
    render_scene_motion(image_a, image_b, search_roi, keypoints, matches, groups, grouped_indices).save(
        debug_path,
        quality=92,
    )

    summary_path = out_path / "summary.json"
    grouped_count = sum(len(indices) for indices in grouped_indices)
    result = SceneMotionResult(
        image_a=str(image_a_path),
        image_b=str(image_b_path),
        roi=search_roi,
        keypoint_count=len(keypoints),
        match_count=len(matches),
        grouped_match_count=grouped_count,
        ungrouped_match_count=max(0, len(matches) - grouped_count),
        groups=groups,
        output_files={
            "scene_motion": str(debug_path),
            "summary": str(summary_path),
        },
    )
    summary_path.write_text(json.dumps(asdict(result), indent=2), encoding="utf-8")
    return result


def clamp_roi(roi: list[int], width: int, height: int) -> list[int]:
    x0, y0, x1, y1 = roi
    x0 = max(0, min(width - 1, int(x0)))
    y0 = max(0, min(height - 1, int(y0)))
    x1 = max(0, min(width - 1, int(x1)))
    y1 = max(0, min(height - 1, int(y1)))
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    return [x0, y0, x1, y1]


def find_motion_groups(
    matches: list[FeatureMatch],
    *,
    max_groups: int,
    min_group_size: int,
    residual_threshold: float,
) -> tuple[list[MotionGroup], list[list[int]]]:
    remaining = set(range(len(matches)))
    groups: list[MotionGroup] = []
    grouped_indices: list[list[int]] = []

    while len(groups) < max_groups and len(remaining) >= min_group_size:
        candidate = find_best_similarity_group(matches, sorted(remaining), residual_threshold)
        if candidate is None or len(candidate) < min_group_size:
            break

        group_id = len(groups)
        group = summarize_group(group_id, matches, candidate)
        groups.append(group)
        grouped_indices.append(candidate)
        remaining.difference_update(candidate)

    return groups, grouped_indices


def find_best_similarity_group(
    matches: list[FeatureMatch],
    indices: list[int],
    residual_threshold: float,
) -> list[int] | None:
    if len(indices) < 4:
        return None

    source = np.array([matches[i].source for i in indices], dtype=np.float64)
    target = np.array([matches[i].target for i in indices], dtype=np.float64)
    src_complex = source[:, 0] + 1j * source[:, 1]
    dst_complex = target[:, 0] + 1j * target[:, 1]

    best_local: np.ndarray | None = None
    best_score: tuple[int, float] | None = None
    for local_i in range(len(indices)):
        for local_j in range(local_i + 1, len(indices)):
            src_delta = src_complex[local_j] - src_complex[local_i]
            if abs(src_delta) < 10.0:
                continue

            transform = (dst_complex[local_j] - dst_complex[local_i]) / src_delta
            transform_scale = abs(transform)
            if transform_scale < 0.35 or transform_scale > 3.8:
                continue

            offset = dst_complex[local_i] - transform * src_complex[local_i]
            predicted = transform * src_complex + offset
            residual = np.abs(predicted - dst_complex)
            inliers = residual <= residual_threshold
            count = int(inliers.sum())
            if count < 4:
                continue

            median_residual = float(np.median(residual[inliers]))
            score = (count, -median_residual)
            if best_score is None or score > best_score:
                best_score = score
                best_local = inliers

    if best_local is None:
        return None

    return [indices[i] for i, is_inlier in enumerate(best_local) if is_inlier]


def summarize_group(group_id: int, matches: list[FeatureMatch], indices: list[int]) -> MotionGroup:
    source = np.array([matches[i].source for i in indices], dtype=np.float64)
    target = np.array([matches[i].target for i in indices], dtype=np.float64)
    deltas = target - source
    center_shift = target.mean(axis=0) - source.mean(axis=0)
    scale = similarity_scale(source, target)
    residual_norm = similarity_residuals(source, target)

    return MotionGroup(
        group_id=group_id,
        match_count=len(indices),
        source_bbox=point_bbox(source),
        target_bbox=point_bbox(target),
        center_shift_px=center_shift.round(4).tolist(),
        median_motion_px=np.median(deltas, axis=0).round(4).tolist(),
        scale=scale,
        median_residual_px=float(np.median(residual_norm)),
        mean_score=float(np.mean([matches[i].score for i in indices])),
        kind_hint=motion_kind_hint(scale, center_shift),
    )


def similarity_residuals(source: np.ndarray, target: np.ndarray) -> np.ndarray:
    if len(source) < 2:
        return np.zeros(len(source), dtype=np.float64)

    source_center = source.mean(axis=0)
    target_center = target.mean(axis=0)
    src_complex = (source[:, 0] - source_center[0]) + 1j * (source[:, 1] - source_center[1])
    dst_complex = (target[:, 0] - target_center[0]) + 1j * (target[:, 1] - target_center[1])
    denominator = np.sum(np.abs(src_complex) ** 2)
    if denominator < 1e-6:
        residual = target - source - (target_center - source_center)
        return np.sqrt((residual * residual).sum(axis=1))

    transform = np.sum(dst_complex * np.conj(src_complex)) / denominator
    offset = (target_center[0] + 1j * target_center[1]) - transform * (
        source_center[0] + 1j * source_center[1]
    )
    predicted = transform * (source[:, 0] + 1j * source[:, 1]) + offset
    observed = target[:, 0] + 1j * target[:, 1]
    return np.abs(predicted - observed)


def point_bbox(points: np.ndarray) -> list[int]:
    x0, y0 = np.floor(points.min(axis=0)).astype(int)
    x1, y1 = np.ceil(points.max(axis=0)).astype(int)
    return [int(x0), int(y0), int(x1), int(y1)]


def motion_kind_hint(scale: float | None, center_shift: np.ndarray) -> str:
    if scale is not None and scale >= 1.06:
        return "expanding_or_nearer"
    if scale is not None and scale <= 0.94:
        return "contracting_or_farther"
    if abs(float(center_shift[0])) >= abs(float(center_shift[1])) * 1.8:
        return "mostly_horizontal_motion"
    if abs(float(center_shift[1])) >= abs(float(center_shift[0])) * 1.8:
        return "mostly_vertical_motion"
    return "mixed_motion"


def render_scene_motion(
    image_a: Image.Image,
    image_b: Image.Image,
    roi: list[int],
    keypoints: list[tuple[int, int]],
    matches: list[FeatureMatch],
    groups: list[MotionGroup],
    grouped_indices: list[list[int]],
) -> Image.Image:
    width, height = image_a.size
    canvas = Image.new("RGB", (width * 2, max(height, image_b.height)), (18, 20, 24))
    canvas.paste(image_a, (0, 0))
    canvas.paste(image_b, (width, 0))
    draw = ImageDraw.Draw(canvas)

    x0, y0, x1, y1 = roi
    draw.rectangle([x0, y0, x1, y1], outline=(190, 190, 190), width=1)

    for x, y in keypoints:
        draw.ellipse([x - 1, y - 1, x + 1, y + 1], fill=(180, 180, 180))

    assigned: dict[int, int] = {}
    for group_index, indices in enumerate(grouped_indices):
        for match_index in indices:
            assigned[match_index] = group_index

    for match_index, match in enumerate(matches):
        if match_index in assigned:
            continue
        draw_match(draw, width, match, (95, 95, 95), line_width=1)

    for group_index, indices in enumerate(grouped_indices):
        color = GROUP_COLORS[group_index % len(GROUP_COLORS)]
        for match_index in indices:
            draw_match(draw, width, matches[match_index], color, line_width=2)

    for group, color in zip(groups, GROUP_COLORS):
        sx0, sy0, sx1, sy1 = group.source_bbox
        tx0, ty0, tx1, ty1 = group.target_bbox
        draw.rectangle([sx0, sy0, sx1, sy1], outline=color, width=2)
        draw.rectangle([tx0 + width, ty0, tx1 + width, ty1], outline=color, width=2)
        draw.text((sx0 + 3, max(0, sy0 - 14)), f"g{group.group_id}:{group.match_count}", fill=color)
        draw.text((tx0 + width + 3, max(0, ty0 - 14)), f"g{group.group_id}", fill=color)

    draw.text((8, 8), "source", fill=(255, 255, 255))
    draw.text((width + 8, 8), "target", fill=(255, 255, 255))
    return canvas


def draw_match(
    draw: ImageDraw.ImageDraw,
    image_width: int,
    match: FeatureMatch,
    color: tuple[int, int, int],
    *,
    line_width: int,
) -> None:
    sx, sy = match.source
    tx, ty = match.target
    draw.line([(sx, sy), (tx + image_width, ty)], fill=color, width=line_width)
    draw.ellipse([sx - 2, sy - 2, sx + 2, sy + 2], fill=color)
    draw.ellipse([tx + image_width - 2, ty - 2, tx + image_width + 2, ty + 2], fill=color)


GROUP_COLORS = [
    (70, 220, 110),
    (70, 180, 245),
    (245, 170, 55),
    (220, 95, 235),
    (245, 235, 80),
    (245, 90, 90),
]
