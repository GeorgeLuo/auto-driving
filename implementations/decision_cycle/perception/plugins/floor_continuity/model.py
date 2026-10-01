from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np


@dataclass(frozen=True)
class FloorContinuityConfig:
    working_width: int = 320
    horizon_ratio: float = 0.40
    edge_margin_ratio: float = 0.03
    seed_x0_ratio: float = 0.30
    seed_x1_ratio: float = 0.70
    seed_y0_ratio: float = 0.78
    seed_y1_ratio: float = 0.96
    color_distance_limit: float = 4.5
    texture_distance_limit: float = 4.0
    edge_quantile: float = 0.92
    minimum_edge_strength: float = 0.24
    minimum_floor_fraction: float = 0.08
    minimum_floor_support_px: int = 8
    minimum_interruption_run_px: int = 6
    minimum_boundary_width_ratio: float = 0.025
    minimum_boundary_confidence: float = 0.65
    max_boundaries: int = 8


@dataclass(frozen=True)
class FloorContinuityAnalysis:
    source_width: int
    source_height: int
    working_width: int
    working_height: int
    floor_mask: np.ndarray
    boundary_mask: np.ndarray
    floor: dict[str, Any] | None
    boundaries: tuple[dict[str, Any], ...]
    measurements: dict[str, Any]


def analyze_floor_continuity(
    rgb: np.ndarray,
    config: FloorContinuityConfig,
) -> FloorContinuityAnalysis:
    source_height, source_width = rgb.shape[:2]
    scale = min(1.0, config.working_width / max(source_width, 1))
    width = max(1, int(round(source_width * scale)))
    height = max(1, int(round(source_height * scale)))
    working_rgb = (
        cv2.resize(rgb, (width, height), interpolation=cv2.INTER_AREA)
        if (width, height) != (source_width, source_height)
        else np.array(rgb, copy=True)
    )

    roi_mask = _analysis_mask(width, height, config)
    features = _image_features(working_rgb)
    seed_mask = _seed_mask(width, height, roi_mask, config)
    model = _floor_model(features, seed_mask)
    candidate_mask, barrier_mask, cue_info = _candidate_floor_mask(
        features,
        roi_mask,
        model,
        config,
    )
    floor_mask = _bottom_connected_floor(candidate_mask, barrier_mask, seed_mask)
    boundary_hits, hit_details = _first_interruptions(
        floor_mask,
        roi_mask,
        features["gradient_unit"],
        config,
    )
    boundaries = _boundary_components(
        boundary_hits,
        hit_details,
        features,
        model,
        config,
    )
    floor = _floor_region(floor_mask)

    roi_pixels = max(int(np.count_nonzero(roi_mask)), 1)
    floor_fraction = float(np.count_nonzero(floor_mask & roi_mask) / roi_pixels)
    center_slice = slice(max(0, int(width * 0.4)), min(width, int(width * 0.6)))
    center_columns = floor_mask[:, center_slice].any(axis=0)
    center_support = float(np.mean(center_columns)) if center_columns.size else 0.0
    floor_confidence = _clamp01(
        0.45 * min(1.0, floor_fraction / max(config.minimum_floor_fraction * 3.0, 1e-6))
        + 0.30 * center_support
        + 0.25 * model["seed_quality"]
    )
    boundary_confidence = max(
        (float(boundary["confidence"]) for boundary in boundaries),
        default=0.0,
    )
    measurements = {
        "source_width_px": source_width,
        "source_height_px": source_height,
        "working_width_px": width,
        "working_height_px": height,
        "floor_fraction_roi": round(floor_fraction, 6),
        "center_floor_support": round(center_support, 6),
        "floor_confidence": round(floor_confidence, 6),
        "boundary_confidence": round(boundary_confidence, 6),
        "boundary_count": len(boundaries),
        "seed_quality": round(float(model["seed_quality"]), 6),
        "seed_clipping_fraction": round(float(model["clipping_fraction"]), 6),
        "blur_score": round(float(model["blur_score"]), 6),
        "edge_threshold": round(float(cue_info["edge_threshold"]), 6),
        "candidate_floor_fraction_roi": round(float(cue_info["candidate_fraction"]), 6),
        "boundaries": [
            {key: value for key, value in boundary.items() if key != "mask"}
            for boundary in boundaries
        ],
    }
    return FloorContinuityAnalysis(
        source_width=source_width,
        source_height=source_height,
        working_width=width,
        working_height=height,
        floor_mask=floor_mask,
        boundary_mask=boundary_hits,
        floor=floor,
        boundaries=tuple(boundaries),
        measurements=measurements,
    )


def _analysis_mask(
    width: int,
    height: int,
    config: FloorContinuityConfig,
) -> np.ndarray:
    margin = max(0, int(round(width * config.edge_margin_ratio)))
    horizon = max(0, min(height - 1, int(round(height * config.horizon_ratio))))
    top_inset = max(margin, int(round(width * max(config.edge_margin_ratio, 0.06))))
    polygon = np.array(
        [
            [margin, height - 1],
            [width - 1 - margin, height - 1],
            [width - 1 - top_inset, horizon],
            [top_inset, horizon],
        ],
        dtype=np.int32,
    )
    mask = np.zeros((height, width), dtype=np.uint8)
    cv2.fillConvexPoly(mask, polygon, 1)
    return mask.astype(bool)


def _seed_mask(
    width: int,
    height: int,
    roi_mask: np.ndarray,
    config: FloorContinuityConfig,
) -> np.ndarray:
    x0 = max(0, min(width - 1, int(round(width * config.seed_x0_ratio))))
    x1 = max(x0 + 1, min(width, int(round(width * config.seed_x1_ratio))))
    y0 = max(0, min(height - 1, int(round(height * config.seed_y0_ratio))))
    y1 = max(y0 + 1, min(height, int(round(height * config.seed_y1_ratio))))
    seed = np.zeros((height, width), dtype=bool)
    seed[y0:y1, x0:x1] = True
    return seed & roi_mask


def _image_features(rgb: np.ndarray) -> dict[str, np.ndarray]:
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
    local_mean = cv2.boxFilter(gray, cv2.CV_32F, (7, 7), normalize=True)
    local_sq_mean = cv2.boxFilter(gray * gray, cv2.CV_32F, (7, 7), normalize=True)
    texture = np.sqrt(np.maximum(local_sq_mean - local_mean * local_mean, 0.0))
    grad_x = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    grad_y = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    gradient = cv2.magnitude(grad_x, grad_y)
    gradient = cv2.GaussianBlur(gradient, (3, 3), 0)
    return {
        "lab": lab,
        "gray": gray,
        "texture": texture,
        "gradient": gradient,
    }


def _floor_model(
    features: dict[str, np.ndarray],
    seed_mask: np.ndarray,
) -> dict[str, Any]:
    lab_seed = features["lab"][seed_mask]
    texture_seed = features["texture"][seed_mask]
    gray_seed = features["gray"][seed_mask]
    if len(lab_seed) == 0:
        raise ValueError("floor seed region is empty")

    luminance = lab_seed[:, 0]
    low, high = np.quantile(luminance, [0.08, 0.92])
    kept = (luminance >= low) & (luminance <= high)
    if int(np.count_nonzero(kept)) >= 8:
        lab_seed = lab_seed[kept]
        texture_seed = texture_seed[kept]
        gray_seed = gray_seed[kept]

    center = np.median(lab_seed, axis=0)
    spread = np.median(np.abs(lab_seed - center), axis=0) * 1.4826
    spread = np.maximum(spread, np.array([7.0, 3.5, 3.5], dtype=np.float32))
    texture_center = float(np.median(texture_seed))
    texture_spread = max(
        float(np.median(np.abs(texture_seed - texture_center)) * 1.4826),
        0.008,
    )
    clipping_fraction = float(np.mean((gray_seed <= 0.02) | (gray_seed >= 0.98)))
    blur_variance = float(cv2.Laplacian(features["gray"], cv2.CV_32F).var())
    blur_score = _clamp01(blur_variance / 0.006)
    seed_dispersion = float(np.mean(np.minimum(spread / np.array([30.0, 18.0, 18.0]), 1.0)))
    seed_quality = _clamp01(
        0.55 * (1.0 - seed_dispersion)
        + 0.30 * (1.0 - clipping_fraction)
        + 0.15 * blur_score
    )
    return {
        "lab_center": center,
        "lab_spread": spread,
        "texture_center": texture_center,
        "texture_spread": texture_spread,
        "clipping_fraction": clipping_fraction,
        "blur_score": blur_score,
        "seed_quality": seed_quality,
    }


def _candidate_floor_mask(
    features: dict[str, np.ndarray],
    roi_mask: np.ndarray,
    model: dict[str, Any],
    config: FloorContinuityConfig,
) -> tuple[np.ndarray, np.ndarray, dict[str, float]]:
    normalized_lab = (
        features["lab"] - model["lab_center"]
    ) / model["lab_spread"]
    color_distance = np.sqrt(np.mean(normalized_lab * normalized_lab, axis=2))
    texture_distance = np.abs(
        features["texture"] - model["texture_center"]
    ) / model["texture_spread"]
    fused_distance = 0.78 * (
        color_distance / max(config.color_distance_limit, 1e-6)
    ) + 0.22 * (
        texture_distance / max(config.texture_distance_limit, 1e-6)
    )
    candidate = (
        (fused_distance <= 1.0)
        & (color_distance <= config.color_distance_limit * 1.15)
        & roi_mask
    )
    candidate = cv2.morphologyEx(
        candidate.astype(np.uint8),
        cv2.MORPH_CLOSE,
        np.ones((3, 3), dtype=np.uint8),
    ).astype(bool) & roi_mask

    roi_gradients = features["gradient"][roi_mask]
    gradient_scale = max(float(np.quantile(roi_gradients, 0.98)), 0.04)
    relative_edge_threshold = max(
        0.34,
        min(
            0.78,
            float(
                np.quantile(
                    np.clip(features["gradient"] / gradient_scale, 0.0, 1.0)[roi_mask],
                    config.edge_quantile,
                )
            ),
        ),
    )
    edge_threshold = max(
        float(config.minimum_edge_strength),
        relative_edge_threshold * gradient_scale,
    )
    gradient_unit = np.clip(
        features["gradient"] / max(edge_threshold, 1e-6), 0.0, 1.0
    )
    barrier = (features["gradient"] >= edge_threshold) & roi_mask
    barrier = cv2.dilate(
        barrier.astype(np.uint8),
        np.ones((3, 3), dtype=np.uint8),
        iterations=1,
    ).astype(bool)
    features["color_distance"] = color_distance
    features["texture_distance"] = texture_distance
    features["gradient_unit"] = gradient_unit
    return candidate, barrier, {
        "edge_threshold": edge_threshold,
        "candidate_fraction": float(np.count_nonzero(candidate) / max(np.count_nonzero(roi_mask), 1)),
    }


def _bottom_connected_floor(
    candidate_mask: np.ndarray,
    barrier_mask: np.ndarray,
    seed_mask: np.ndarray,
) -> np.ndarray:
    passable = candidate_mask & ~barrier_mask
    count, labels = cv2.connectedComponents(passable.astype(np.uint8), connectivity=8)
    if count <= 1:
        return np.zeros_like(passable)
    seed_labels = np.unique(labels[seed_mask & passable])
    seed_labels = seed_labels[seed_labels > 0]
    if seed_labels.size == 0:
        return np.zeros_like(passable)
    connected_labels = np.zeros(count, dtype=bool)
    connected_labels[seed_labels] = True
    floor = connected_labels[labels]
    floor = cv2.morphologyEx(
        floor.astype(np.uint8),
        cv2.MORPH_CLOSE,
        np.ones((3, 3), dtype=np.uint8),
    ).astype(bool)
    return floor & candidate_mask


def _first_interruptions(
    floor_mask: np.ndarray,
    roi_mask: np.ndarray,
    gradient_unit: np.ndarray,
    config: FloorContinuityConfig,
) -> tuple[np.ndarray, dict[int, dict[str, float]]]:
    height, width = floor_mask.shape
    min_floor = max(1, int(config.minimum_floor_support_px))
    min_run = max(1, int(config.minimum_interruption_run_px))
    hits = np.zeros_like(floor_mask, dtype=np.uint8)
    details: dict[int, dict[str, float]] = {}
    for x in range(width):
        floor_support = 0
        non_floor_run = 0
        run_start = -1
        for y in range(height - 1, -1, -1):
            if not roi_mask[y, x]:
                break
            if floor_mask[y, x]:
                floor_support += 1
                non_floor_run = 0
                continue
            if floor_support < min_floor:
                non_floor_run = 0
                continue
            if non_floor_run == 0:
                run_start = y
            non_floor_run += 1
            if non_floor_run >= min_run:
                y0 = max(0, run_start - 2)
                y1 = min(height, run_start + 3)
                hits[y0:y1, x] = 1
                details[x] = {
                    "y": float(run_start),
                    "floor_support": float(floor_support),
                    "edge_support": float(np.max(gradient_unit[y0:y1, x])),
                    "run_support": float(non_floor_run),
                }
                break

    close_width = max(3, int(round(width * config.minimum_boundary_width_ratio)))
    if close_width % 2 == 0:
        close_width += 1
    hits = cv2.morphologyEx(
        hits,
        cv2.MORPH_CLOSE,
        np.ones((3, close_width), dtype=np.uint8),
    )
    return hits.astype(bool), details


def _boundary_components(
    boundary_mask: np.ndarray,
    hit_details: dict[int, dict[str, float]],
    features: dict[str, np.ndarray],
    model: dict[str, Any],
    config: FloorContinuityConfig,
) -> list[dict[str, Any]]:
    if not boundary_mask.any():
        return []
    height, width = boundary_mask.shape
    count, labels, stats, _centroids = cv2.connectedComponentsWithStats(
        boundary_mask.astype(np.uint8), connectivity=8
    )
    minimum_width = max(3, int(round(width * config.minimum_boundary_width_ratio)))
    boundaries: list[dict[str, Any]] = []
    for label in range(1, count):
        x = int(stats[label, cv2.CC_STAT_LEFT])
        y = int(stats[label, cv2.CC_STAT_TOP])
        component_width = int(stats[label, cv2.CC_STAT_WIDTH])
        component_height = int(stats[label, cv2.CC_STAT_HEIGHT])
        if component_width < minimum_width:
            continue
        component_mask = labels == label
        columns = [column for column in range(x, x + component_width) if column in hit_details]
        if len(columns) < minimum_width:
            continue
        edge_support = float(np.mean([hit_details[column]["edge_support"] for column in columns]))
        floor_support = float(
            np.mean([
                min(1.0, hit_details[column]["floor_support"] / max(height * 0.35, 1.0))
                for column in columns
            ])
        )
        ys = np.array([hit_details[column]["y"] for column in columns], dtype=np.float32)
        vertical_consistency = _clamp01(1.0 - float(np.std(ys)) / max(height * 0.08, 1.0))
        width_fraction = component_width / max(width, 1)
        width_support = min(1.0, width_fraction / max(config.minimum_boundary_width_ratio * 5.0, 1e-6))
        sample_y = np.clip(ys.astype(np.int32), 0, height - 1)
        sample_x = np.array(columns, dtype=np.int32)
        color_discontinuity = float(np.mean(np.minimum(features["color_distance"][sample_y, sample_x] / 4.0, 1.0)))
        texture_discontinuity = float(np.mean(np.minimum(features["texture_distance"][sample_y, sample_x] / 4.0, 1.0)))
        cue_agreement = float(np.mean([
            edge_support,
            color_discontinuity,
            texture_discontinuity,
        ]))
        ambiguity = max(0.0, 0.45 - cue_agreement)
        confidence = _clamp01(
            0.24 * width_support
            + 0.20 * floor_support
            + 0.22 * edge_support
            + 0.14 * vertical_consistency
            + 0.10 * cue_agreement
            + 0.10 * model["seed_quality"]
            - 0.15 * (1.0 - model["blur_score"])
            - 0.20 * ambiguity
        )
        if confidence < config.minimum_boundary_confidence:
            continue
        contours, _ = cv2.findContours(
            component_mask.astype(np.uint8),
            cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_SIMPLE,
        )
        contour = max(contours, key=cv2.contourArea) if contours else None
        polygon = _normalized_contour(contour, width, height) if contour is not None else None
        boundaries.append({
            "mask": component_mask,
            "bbox_xyxy_norm": _normalized_bbox(
                x,
                y,
                component_width,
                component_height,
                width,
                height,
            ),
            "polygon_xy_norm": polygon,
            "centroid_xy_norm": (
                round((x + component_width / 2.0) / max(width - 1, 1), 5),
                round(float(np.median(ys)) / max(height - 1, 1), 5),
            ),
            "confidence": round(confidence, 5),
            "width_fraction": round(width_fraction, 6),
            "floor_support_below": round(floor_support, 5),
            "edge_agreement": round(edge_support, 5),
            "vertical_consistency": round(vertical_consistency, 5),
            "color_discontinuity": round(color_discontinuity, 5),
            "texture_discontinuity": round(texture_discontinuity, 5),
            "cue_agreement": round(cue_agreement, 5),
            "ambiguity": round(ambiguity, 5),
        })
    boundaries.sort(
        key=lambda item: (item["confidence"], item["width_fraction"]),
        reverse=True,
    )
    return boundaries[: max(1, int(config.max_boundaries))]


def _floor_region(floor_mask: np.ndarray) -> dict[str, Any] | None:
    contours, _ = cv2.findContours(
        floor_mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    if not contours:
        return None
    contour = max(contours, key=cv2.contourArea)
    x, y, width, height = cv2.boundingRect(contour)
    image_height, image_width = floor_mask.shape
    return {
        "bbox_xyxy_norm": _normalized_bbox(
            x, y, width, height, image_width, image_height
        ),
        "polygon_xy_norm": _normalized_contour(contour, image_width, image_height),
    }


def _normalized_bbox(
    x: int,
    y: int,
    width: int,
    height: int,
    image_width: int,
    image_height: int,
) -> tuple[float, float, float, float]:
    return (
        round(x / max(image_width - 1, 1), 5),
        round(y / max(image_height - 1, 1), 5),
        round((x + width - 1) / max(image_width - 1, 1), 5),
        round((y + height - 1) / max(image_height - 1, 1), 5),
    )


def _normalized_contour(
    contour: np.ndarray,
    width: int,
    height: int,
) -> tuple[tuple[float, float], ...] | None:
    if contour is None or len(contour) < 3:
        return None
    epsilon = max(1.0, cv2.arcLength(contour, True) * 0.01)
    points = cv2.approxPolyDP(contour, epsilon, True).reshape(-1, 2)
    if len(points) < 3:
        return None
    if len(points) > 64:
        points = points[np.linspace(0, len(points) - 1, 64, dtype=int)]
    return tuple(
        (
            round(float(x) / max(width - 1, 1), 5),
            round(float(y) / max(height - 1, 1), 5),
        )
        for x, y in points
    )


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
