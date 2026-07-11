from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw


@dataclass
class FeatureMatch:
    source: list[float]
    target: list[float]
    score: float
    inlier: bool = True


@dataclass
class FeatureTrackingResult:
    image_a: str
    image_b: str
    bbox: list[int]
    keypoint_count: int
    match_count: int
    inlier_count: int
    median_dx_px: float | None
    median_dy_px: float | None
    center_shift_px: list[float] | None
    scale: float | None
    output_files: dict[str, str]
    matches: list[FeatureMatch]


def track_features(
    image_a_path: str | Path,
    image_b_path: str | Path,
    bbox: list[int],
    out_dir: str | Path,
    *,
    max_features: int = 80,
    min_distance: int = 7,
    patch_radius: int = 5,
    search_radius: int = 70,
    min_score: float = 0.72,
) -> FeatureTrackingResult:
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    image_a = Image.open(image_a_path).convert("RGB")
    image_b = Image.open(image_b_path).convert("RGB")
    gray_a = grayscale(image_a)
    gray_b = grayscale(image_b)

    keypoints = detect_keypoints(
        gray_a,
        bbox,
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
    mark_inliers(matches)

    debug_path = out_path / "matches.jpg"
    render_matches(image_a, image_b, bbox, keypoints, matches).save(debug_path, quality=92)

    summary_path = out_path / "summary.json"
    result = make_result(image_a_path, image_b_path, bbox, keypoints, matches, debug_path, summary_path)
    summary_path.write_text(json.dumps(asdict(result), indent=2), encoding="utf-8")
    return result


def grayscale(image: Image.Image) -> np.ndarray:
    arr = np.asarray(image).astype(np.float32)
    return 0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2]


def detect_keypoints(
    gray: np.ndarray,
    bbox: list[int],
    *,
    max_features: int,
    min_distance: int,
    patch_radius: int,
) -> list[tuple[int, int]]:
    x0, y0, x1, y1 = bbox
    height, width = gray.shape
    margin = patch_radius + 2
    x0 = max(x0 + margin, margin)
    y0 = max(y0 + margin, margin)
    x1 = min(x1 - margin, width - margin - 1)
    y1 = min(y1 - margin, height - margin - 1)
    if x1 <= x0 or y1 <= y0:
        return []

    dx = np.zeros_like(gray)
    dy = np.zeros_like(gray)
    dx[:, 1:-1] = gray[:, 2:] - gray[:, :-2]
    dy[1:-1, :] = gray[2:, :] - gray[:-2, :]

    ixx = box_sum(dx * dx, radius=2)
    iyy = box_sum(dy * dy, radius=2)
    ixy = box_sum(dx * dy, radius=2)
    trace = ixx + iyy
    determinant = ixx * iyy - ixy * ixy
    score = determinant / np.maximum(trace, 1e-6)
    score[:y0, :] = 0
    score[y1 + 1:, :] = 0
    score[:, :x0] = 0
    score[:, x1 + 1:] = 0

    candidates = np.argwhere(score > np.percentile(score[y0:y1 + 1, x0:x1 + 1], 80))
    candidates = sorted(candidates, key=lambda p: score[p[0], p[1]], reverse=True)

    selected: list[tuple[int, int]] = []
    min_distance_sq = min_distance * min_distance
    for y, x in candidates:
        point = (int(x), int(y))
        if all((point[0] - px) ** 2 + (point[1] - py) ** 2 >= min_distance_sq for px, py in selected):
            selected.append(point)
            if len(selected) >= max_features:
                break
    return selected


def box_sum(values: np.ndarray, radius: int) -> np.ndarray:
    padded = np.pad(values, radius, mode="edge")
    integral = padded.cumsum(axis=0).cumsum(axis=1)
    integral = np.pad(integral, ((1, 0), (1, 0)), mode="constant")
    size = radius * 2 + 1
    return (
        integral[size:, size:]
        - integral[:-size, size:]
        - integral[size:, :-size]
        + integral[:-size, :-size]
    )


def match_keypoints(
    gray_a: np.ndarray,
    gray_b: np.ndarray,
    keypoints: list[tuple[int, int]],
    *,
    patch_radius: int,
    search_radius: int,
    min_score: float,
) -> list[FeatureMatch]:
    matches: list[FeatureMatch] = []
    height, width = gray_b.shape
    patch_size = patch_radius * 2 + 1

    for x, y in keypoints:
        patch = extract_patch(gray_a, x, y, patch_radius)
        if patch is None:
            continue
        patch = patch.astype(np.float32)
        patch_norm = patch - patch.mean()
        patch_scale = float(np.sqrt((patch_norm * patch_norm).sum()))
        if patch_scale < 1e-6:
            continue

        left = max(patch_radius, x - search_radius)
        right = min(width - patch_radius - 1, x + search_radius)
        top = max(patch_radius, y - search_radius)
        bottom = min(height - patch_radius - 1, y + search_radius)
        if right <= left or bottom <= top:
            continue

        region = gray_b[top - patch_radius:bottom + patch_radius + 1,
                        left - patch_radius:right + patch_radius + 1].astype(np.float32)
        windows = np.lib.stride_tricks.sliding_window_view(region, (patch_size, patch_size))
        means = windows.mean(axis=(-1, -2), keepdims=True)
        centered = windows - means
        norms = np.sqrt((centered * centered).sum(axis=(-1, -2)))
        scores = (centered * patch_norm).sum(axis=(-1, -2)) / np.maximum(norms * patch_scale, 1e-6)

        best_flat = int(np.argmax(scores))
        best_y, best_x = np.unravel_index(best_flat, scores.shape)
        score = float(scores[best_y, best_x])
        if score < min_score:
            continue

        matches.append(FeatureMatch(
            source=[float(x), float(y)],
            target=[float(left + best_x), float(top + best_y)],
            score=score,
        ))
    return matches


def extract_patch(gray: np.ndarray, x: int, y: int, radius: int) -> np.ndarray | None:
    height, width = gray.shape
    if x - radius < 0 or x + radius >= width or y - radius < 0 or y + radius >= height:
        return None
    return gray[y - radius:y + radius + 1, x - radius:x + radius + 1]


def mark_inliers(matches: list[FeatureMatch]) -> None:
    if len(matches) < 4:
        return
    source = np.array([m.source for m in matches], dtype=np.float64)
    target = np.array([m.target for m in matches], dtype=np.float64)
    src_complex = source[:, 0] + 1j * source[:, 1]
    dst_complex = target[:, 0] + 1j * target[:, 1]

    best_inliers: np.ndarray | None = None
    best_score: tuple[int, float] | None = None
    threshold = 8.0

    for i in range(len(matches)):
        for j in range(i + 1, len(matches)):
            src_delta = src_complex[j] - src_complex[i]
            if abs(src_delta) < 8.0:
                continue
            transform = (dst_complex[j] - dst_complex[i]) / src_delta
            if abs(transform) < 0.4 or abs(transform) > 3.5:
                continue
            offset = dst_complex[i] - transform * src_complex[i]
            predicted = transform * src_complex + offset
            residual = np.abs(predicted - dst_complex)
            inliers = residual <= threshold
            count = int(inliers.sum())
            if count < 4:
                continue
            score = (count, -float(np.median(residual[inliers])))
            if best_score is None or score > best_score:
                best_score = score
                best_inliers = inliers

    if best_inliers is None:
        deltas = target - source
        med = np.median(deltas, axis=0)
        residual = np.sqrt(((deltas - med) ** 2).sum(axis=1))
        mad = np.median(np.abs(residual - np.median(residual)))
        threshold = max(10.0, 2.5 * 1.4826 * mad)
        best_inliers = residual <= threshold

    for match, inlier in zip(matches, best_inliers):
        match.inlier = bool(inlier)


def make_result(
    image_a_path: str | Path,
    image_b_path: str | Path,
    bbox: list[int],
    keypoints: list[tuple[int, int]],
    matches: list[FeatureMatch],
    debug_path: Path,
    summary_path: Path,
) -> FeatureTrackingResult:
    inliers = [m for m in matches if m.inlier]
    median_dx = median_dy = None
    center_shift = None
    scale = None
    if inliers:
        source = np.array([m.source for m in inliers], dtype=np.float64)
        target = np.array([m.target for m in inliers], dtype=np.float64)
        deltas = target - source
        median_dx = float(np.median(deltas[:, 0]))
        median_dy = float(np.median(deltas[:, 1]))
        source_center = source.mean(axis=0)
        target_center = target.mean(axis=0)
        center_shift = (target_center - source_center).round(4).tolist()

        scale = similarity_scale(source, target)

    return FeatureTrackingResult(
        image_a=str(image_a_path),
        image_b=str(image_b_path),
        bbox=bbox,
        keypoint_count=len(keypoints),
        match_count=len(matches),
        inlier_count=len(inliers),
        median_dx_px=median_dx,
        median_dy_px=median_dy,
        center_shift_px=center_shift,
        scale=scale,
        output_files={
            "matches": str(debug_path),
            "summary": str(summary_path),
        },
        matches=matches,
    )


def similarity_scale(source: np.ndarray, target: np.ndarray) -> float | None:
    if len(source) < 2:
        return None
    source_center = source.mean(axis=0)
    target_center = target.mean(axis=0)
    src_complex = (source[:, 0] - source_center[0]) + 1j * (source[:, 1] - source_center[1])
    dst_complex = (target[:, 0] - target_center[0]) + 1j * (target[:, 1] - target_center[1])
    denominator = np.sum(np.abs(src_complex) ** 2)
    if denominator < 1e-6:
        return None
    transform = np.sum(dst_complex * np.conj(src_complex)) / denominator
    return float(abs(transform))


def render_matches(
    image_a: Image.Image,
    image_b: Image.Image,
    bbox: list[int],
    keypoints: list[tuple[int, int]],
    matches: list[FeatureMatch],
) -> Image.Image:
    width, height = image_a.size
    canvas = Image.new("RGB", (width * 2, max(height, image_b.height)), (18, 20, 24))
    canvas.paste(image_a, (0, 0))
    canvas.paste(image_b, (width, 0))
    draw = ImageDraw.Draw(canvas)

    x0, y0, x1, y1 = bbox
    draw.rectangle([x0, y0, x1, y1], outline=(255, 210, 40), width=2)
    for x, y in keypoints:
        draw.ellipse([x - 2, y - 2, x + 2, y + 2], fill=(255, 210, 40))

    for match in matches:
        sx, sy = match.source
        tx, ty = match.target
        color = (70, 220, 110) if match.inlier else (235, 70, 55)
        draw.line([(sx, sy), (tx + width, ty)], fill=color, width=1)
        draw.ellipse([sx - 2, sy - 2, sx + 2, sy + 2], fill=color)
        draw.ellipse([tx + width - 2, ty - 2, tx + width + 2, ty + 2], fill=color)

    draw.text((8, 8), "source", fill=(255, 255, 255))
    draw.text((width + 8, 8), "target", fill=(255, 255, 255))
    return canvas
