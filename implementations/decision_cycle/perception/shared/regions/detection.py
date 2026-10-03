"""Coherent-color region proposals from mean-shift smoothing and connected components."""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np


def detect_regions(
    rgb: np.ndarray,
    *,
    working_width: int,
    spatial_radius: int,
    color_radius: int,
    min_area_fraction: float,
    max_area_fraction: float,
    max_regions: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source_height, source_width = rgb.shape[:2]
    scale = min(1.0, working_width / source_width)
    width = max(1, int(round(source_width * scale)))
    height = max(1, int(round(source_height * scale)))
    working_rgb = cv2.resize(rgb, (width, height), interpolation=cv2.INTER_AREA) if scale < 1 else rgb.copy()
    working_bgr = cv2.cvtColor(working_rgb, cv2.COLOR_RGB2BGR)
    smoothed_bgr = cv2.pyrMeanShiftFiltering(
        working_bgr,
        sp=spatial_radius,
        sr=color_radius,
        maxLevel=1,
    )
    smoothed_rgb = cv2.cvtColor(smoothed_bgr, cv2.COLOR_BGR2RGB)
    lab = cv2.cvtColor(smoothed_bgr, cv2.COLOR_BGR2LAB)
    quantized = (
        (lab[..., 0].astype(np.int32) // 24) * 169
        + (lab[..., 1].astype(np.int32) // 20) * 13
        + (lab[..., 2].astype(np.int32) // 20)
    )
    image_area = max(1, width * height)
    min_area = max(4, int(round(image_area * min_area_fraction)))
    max_area = max(min_area, int(round(image_area * max_area_fraction)))
    kernel = np.ones((3, 3), dtype=np.uint8)
    proposals: list[dict[str, Any]] = []
    component_count = 0

    values, counts = np.unique(quantized, return_counts=True)
    for value, count in zip(values, counts):
        if count < min_area:
            continue
        mask = (quantized == value).astype(np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        label_count, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
        component_count += max(0, label_count - 1)
        for label in range(1, label_count):
            area = int(stats[label, cv2.CC_STAT_AREA])
            if area < min_area or area > max_area:
                continue
            x = int(stats[label, cv2.CC_STAT_LEFT])
            y = int(stats[label, cv2.CC_STAT_TOP])
            component_width = int(stats[label, cv2.CC_STAT_WIDTH])
            component_height = int(stats[label, cv2.CC_STAT_HEIGHT])
            if component_width < 4 or component_height < 4:
                continue
            component_mask = labels == label
            contours, _ = cv2.findContours(
                component_mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )
            if not contours:
                continue
            contour = max(contours, key=cv2.contourArea)
            hull_area = max(float(cv2.contourArea(cv2.convexHull(contour))), 1.0)
            solidity = min(1.0, area / hull_area)
            pixels = lab[component_mask].astype(np.float32)
            channel_std = float(np.mean(np.std(pixels, axis=0))) if len(pixels) else 255.0
            coherence = max(0.0, min(1.0, 1.0 - channel_std / 32.0))
            support = min(1.0, area / max(min_area * 8.0, 1.0))
            confidence = round(0.45 * coherence + 0.35 * solidity + 0.20 * support, 5)
            contour_points = _normalized_contour(contour, width, height)
            centroid = centroids[label]
            proposals.append({
                "mask": component_mask,
                "area_fraction": round(area / image_area, 6),
                "bbox": (
                    round(x / max(width - 1, 1), 5),
                    round(y / max(height - 1, 1), 5),
                    round((x + component_width - 1) / max(width - 1, 1), 5),
                    round((y + component_height - 1) / max(height - 1, 1), 5),
                ),
                "centroid": (
                    round(float(centroid[0]) / max(width - 1, 1), 5),
                    round(float(centroid[1]) / max(height - 1, 1), 5),
                ),
                "contour": contour_points,
                "confidence": confidence,
                "color_coherence": round(coherence, 5),
                "solidity": round(solidity, 5),
                "touches_lower_image": bool(y + component_height - 1 >= height * 0.85),
            })

    proposals.sort(key=lambda item: (item["confidence"], item["area_fraction"]), reverse=True)
    return proposals[:max_regions], {
        "working_width": width,
        "working_height": height,
        "component_count": component_count,
        "smoothed_rgb": smoothed_rgb,
    }


def _normalized_contour(contour: np.ndarray, width: int, height: int) -> list[list[float]]:
    epsilon = max(1.0, cv2.arcLength(contour, True) * 0.01)
    points = cv2.approxPolyDP(contour, epsilon, True).reshape(-1, 2)
    if len(points) > 64:
        points = points[np.linspace(0, len(points) - 1, 64, dtype=int)]
    return [
        [
            round(float(x) / max(width - 1, 1), 5),
            round(float(y) / max(height - 1, 1), 5),
        ]
        for x, y in points
    ]
