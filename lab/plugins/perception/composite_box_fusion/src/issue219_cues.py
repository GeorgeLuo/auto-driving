
# selected=['_adaptive_contours', '_canny_morph', '_corner_junctions', '_hough_fragments', '_partial_contour', '_photometric_windows', '_quad_rectangularity'] needed=['_adaptive_contours', '_bbox_area', '_bbox_iou', '_canny_morph', '_contour_candidate', '_corner_junctions', '_cv_box_thing', '_dedupe_cv_things', '_gray_variants', '_hough_fragments', '_normalized_xywh', '_partial_contour', '_photometric_windows', '_quad_rectangularity', '_quad_score', '_working_rgb', '_zone_from_bbox']
from __future__ import annotations

import math
from typing import Any

import cv2
import numpy as np

from autonomy.perception import PerceivedThing, ViewLocation

def _working_rgb(rgb: np.ndarray, working_width: int) -> np.ndarray:
    source_height, source_width = rgb.shape[:2]
    scale = min(1.0, working_width / max(source_width, 1))
    if scale >= 1.0:
        return rgb
    return cv2.resize(
        rgb,
        (max(1, int(round(source_width * scale))), max(1, int(round(source_height * scale)))),
        interpolation=cv2.INTER_AREA,
    )

def _gray_variants(rgb: np.ndarray) -> dict[str, np.ndarray]:
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    return {
        "gray": gray,
        "lab_l": lab[..., 0],
        "hsv_v": hsv[..., 2],
        "clahe": clahe,
    }

def _cv_box_thing(
    source: str,
    index: int,
    bbox: tuple[float, float, float, float],
    confidence: float,
    *,
    kind: str,
    label: str,
    properties: dict[str, Any],
) -> PerceivedThing:
    return PerceivedThing(
        thing_id=f"{source}_candidate_{index:03d}",
        kind=kind,
        label=label,
        location=ViewLocation(
            frame="image",
            zone=_zone_from_bbox(bbox),
            bbox_xyxy_norm=bbox,
        ),
        confidence=max(0.0, min(1.0, float(confidence))),
        properties={
            "evidence": source,
            "area_fraction": round(_bbox_area(bbox), 6),
            "source": source,
            **properties,
        },
    )

def _normalized_xywh(
    x: int,
    y: int,
    box_width: int,
    box_height: int,
    width: int,
    height: int,
) -> tuple[float, float, float, float]:
    values = (
        x / max(width - 1, 1),
        y / max(height - 1, 1),
        (x + box_width - 1) / max(width - 1, 1),
        (y + box_height - 1) / max(height - 1, 1),
    )
    return tuple(round(max(0.0, min(1.0, value)), 5) for value in values)

def _dedupe_cv_things(
    things: list[PerceivedThing],
    *,
    threshold: float,
    max_boxes: int,
) -> list[PerceivedThing]:
    ordered = sorted(things, key=lambda item: item.confidence, reverse=True)
    result: list[PerceivedThing] = []
    for thing in ordered:
        bbox = thing.location.bbox_xyxy_norm
        if bbox is None:
            continue
        if any(
            _bbox_iou(bbox, existing.location.bbox_xyxy_norm or (0.0, 0.0, 0.0, 0.0)) >= threshold
            for existing in result
        ):
            continue
        result.append(thing)
        if len(result) >= max_boxes:
            break
    return result

def _contour_candidate(
    source: str,
    index: int,
    contour: np.ndarray,
    mask: np.ndarray,
    *,
    params: dict[str, Any],
    min_area_fraction: float,
    max_area_fraction: float,
    min_width: int,
    min_height: int,
) -> PerceivedThing | None:
    height, width = mask.shape[:2]
    x, y, box_width, box_height = cv2.boundingRect(contour)
    area_fraction = box_width * box_height / max(float(width * height), 1.0)
    if (
        area_fraction < min_area_fraction
        or area_fraction > max_area_fraction
        or box_width < min_width
        or box_height < min_height
    ):
        return None
    contour_area = max(float(cv2.contourArea(contour)), 1.0)
    fill = min(1.0, contour_area / max(float(box_width * box_height), 1.0))
    perimeter = max(float(cv2.arcLength(contour, True)), 1.0)
    approx = cv2.approxPolyDP(contour, 0.025 * perimeter, True)
    edge_density = float(
        np.count_nonzero(mask[y : y + box_height, x : x + box_width])
    ) / max(float(box_width * box_height), 1.0)
    compact = 1.0 - min(
        1.0,
        abs(math.log(max(box_width / max(box_height, 1), 1e-6))) / 2.5,
    )
    confidence = (
        0.25
        + 0.30 * min(1.0, fill * 2.0)
        + 0.25 * min(1.0, edge_density * 4.0)
        + 0.20 * compact
    )
    bbox = _normalized_xywh(x, y, box_width, box_height, width, height)
    return _cv_box_thing(
        source,
        index,
        bbox,
        confidence,
        kind="contour_fragment",
        label=f"{source}: contour fragment",
        properties={
            **params,
            "vertices": len(approx),
            "fill": round(fill, 4),
            "edge_density": round(edge_density, 4),
            "working_width": width,
            "working_height": height,
        },
    )

def _canny_morph(
    rgb: np.ndarray,
    *,
    working_width: int,
    max_boxes: int,
    shared_blur_kernel: int = 0,
    shared_close_kernel: int = 0,
    shared_min_width_px: int = 0,
    shared_min_height_px: int = 0,
) -> list[PerceivedThing]:
    working = _working_rgb(rgb, working_width)
    outputs: list[PerceivedThing] = []
    settings = (
        (35, 105, 5, 7, 1, 0),
        (55, 145, 3, 5, 2, 0),
        (75, 190, 5, 9, 1, 1),
    )
    for channel_name, channel in _gray_variants(working).items():
        for low, high, blur, close_kernel, close_iter, open_iter in settings:
            if shared_blur_kernel > 0:
                blur = shared_blur_kernel
            if shared_close_kernel > 0:
                close_kernel = shared_close_kernel
            blurred = cv2.GaussianBlur(channel, (blur, blur), 0)
            edges = cv2.Canny(blurred, low, high, L2gradient=True)
            kernel = cv2.getStructuringElement(
                cv2.MORPH_RECT,
                (close_kernel, close_kernel),
            )
            closed = cv2.morphologyEx(
                edges,
                cv2.MORPH_CLOSE,
                kernel,
                iterations=close_iter,
            )
            if open_iter:
                closed = cv2.morphologyEx(
                    closed,
                    cv2.MORPH_OPEN,
                    np.ones((3, 3), dtype=np.uint8),
                    iterations=open_iter,
                )
            contours, _ = cv2.findContours(
                closed,
                cv2.RETR_LIST,
                cv2.CHAIN_APPROX_SIMPLE,
            )
            for contour in contours:
                thing = _contour_candidate(
                    "canny_morph",
                    len(outputs),
                    contour,
                    closed,
                    params={
                        "channel": channel_name,
                        "canny": [low, high],
                        "blur": blur,
                        "close_kernel": close_kernel,
                        "close_iter": close_iter,
                        "open_iter": open_iter,
                        "retrieval": "list",
                    },
                    min_area_fraction=0.0025,
                    max_area_fraction=0.28,
                    min_width=max(1, shared_min_width_px or 18),
                    min_height=max(1, shared_min_height_px or 18),
                )
                if thing is not None:
                    outputs.append(thing)
    return _dedupe_cv_things(outputs, threshold=0.92, max_boxes=max_boxes)

def _adaptive_contours(
    rgb: np.ndarray,
    *,
    working_width: int,
    max_boxes: int,
) -> list[PerceivedThing]:
    """Keep loose adaptive-threshold fragments as a separate geometry cue."""
    working = _working_rgb(rgb, working_width)
    lab = cv2.cvtColor(working, cv2.COLOR_RGB2LAB)
    gray = cv2.cvtColor(working, cv2.COLOR_RGB2GRAY)
    outputs: list[PerceivedThing] = []
    for channel_name, channel in (("lab_l", lab[..., 0]), ("gray", gray)):
        binary = cv2.adaptiveThreshold(
            channel,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            51,
            5,
        )
        binary = cv2.morphologyEx(
            binary,
            cv2.MORPH_CLOSE,
            np.ones((3, 3), dtype=np.uint8),
            iterations=1,
        )
        contours, _ = cv2.findContours(
            binary,
            cv2.RETR_LIST,
            cv2.CHAIN_APPROX_SIMPLE,
        )
        for contour in contours:
            thing = _contour_candidate(
                "adaptive_contours",
                len(outputs),
                contour,
                binary,
                params={
                    "channel": channel_name,
                    "mode": "adaptive_threshold",
                    "adaptive_block": 51,
                    "adaptive_constant": 5,
                    "close_kernel": 3,
                    "close_iter": 1,
                    "retrieval": "list",
                },
                min_area_fraction=0.00035,
                max_area_fraction=0.19,
                min_width=15,
                min_height=15,
            )
            if thing is not None:
                outputs.append(thing)
    return _dedupe_cv_things(outputs, threshold=0.62, max_boxes=max_boxes)

def _corner_junctions(
    rgb: np.ndarray,
    *,
    working_width: int,
    max_boxes: int,
) -> list[PerceivedThing]:
    """Emit local corner clusters and aligned corner-pair ROIs, not rectangles."""
    working = _working_rgb(rgb, working_width)
    gray = cv2.cvtColor(working, cv2.COLOR_RGB2GRAY)
    points = cv2.goodFeaturesToTrack(
        gray,
        maxCorners=600,
        qualityLevel=0.006,
        minDistance=9,
        blockSize=5,
    )
    if points is None:
        return []
    points_xy = [tuple(float(value) for value in point[0]) for point in points]
    response = cv2.cornerMinEigenVal(gray, blockSize=5, ksize=3)
    response_max = max(float(np.max(response)), 1e-6)
    height, width = gray.shape[:2]
    outputs: list[PerceivedThing] = []

    for index, (px, py) in enumerate(points_xy):
        members = [
            member_index
            for member_index, (qx, qy) in enumerate(points_xy)
            if math.hypot(px - qx, py - qy) <= 96.0
        ]
        if len(members) < 3:
            continue
        xs = [points_xy[member_index][0] for member_index in members]
        ys = [points_xy[member_index][1] for member_index in members]
        x1 = int(math.floor(min(xs) - 20.0))
        y1 = int(math.floor(min(ys) - 20.0))
        x2 = int(math.ceil(max(xs) + 20.0))
        y2 = int(math.ceil(max(ys) + 20.0))
        box_width = x2 - x1
        box_height = y2 - y1
        density = len(members) / max(
            1.0,
            (box_width * box_height) / 10000.0,
        )
        corner_response = float(response[int(round(py)), int(round(px))]) / response_max
        score = (
            0.27
            + 0.42 * min(1.0, len(members) / 12.0)
            + 0.18 * min(1.0, density)
            + 0.13 * min(1.0, corner_response)
        )
        bbox = _normalized_xywh(x1, y1, box_width, box_height, width, height)
        if _bbox_area(bbox) <= 0.00025 or _bbox_area(bbox) > 0.28:
            continue
        outputs.append(
            _cv_box_thing(
                "corner_junctions",
                len(outputs),
                bbox,
                score,
                kind="corner_support",
                label="corner_junctions: local support",
                properties={
                    "anchor_xy_px": [round(px, 2), round(py, 2)],
                    "member_count": len(members),
                    "cluster_radius_px": 96,
                    "cluster_padding_px": 20,
                    "corner_response": round(corner_response, 4),
                    "working_width": width,
                    "working_height": height,
                },
            )
        )

    for left_index, (x1, y1) in enumerate(points_xy):
        for right_index in range(left_index + 1, len(points_xy)):
            x2, y2 = points_xy[right_index]
            dx = abs(x2 - x1)
            dy = abs(y2 - y1)
            distance = math.hypot(dx, dy)
            if distance < 24.0 or distance > 360.0:
                continue
            angle = abs(math.degrees(math.atan2(y2 - y1, x2 - x1))) % 180.0
            axis_bonus = max(
                0.0,
                1.0 - min(abs(angle), abs(angle - 90.0), abs(angle - 180.0)) / 45.0,
            )
            if axis_bonus < 0.06:
                continue
            pad = 16
            box = (
                int(math.floor(min(x1, x2) - pad)),
                int(math.floor(min(y1, y2) - pad)),
                int(math.ceil(max(x1, x2) + pad)),
                int(math.ceil(max(y1, y2) + pad)),
            )
            box_width = box[2] - box[0]
            box_height = box[3] - box[1]
            bbox = _normalized_xywh(
                box[0], box[1], box_width, box_height, width, height
            )
            if _bbox_area(bbox) <= 0.00025 or _bbox_area(bbox) > 0.28:
                continue
            support = (
                float(response[int(round(y1)), int(round(x1))])
                + float(response[int(round(y2)), int(round(x2))])
            ) / (2.0 * response_max)
            score = (
                0.23
                + 0.28 * axis_bonus
                + 0.25 * min(1.0, distance / 260.0)
                + 0.24 * min(1.0, support)
            )
            outputs.append(
                _cv_box_thing(
                    "corner_junctions",
                    len(outputs),
                    bbox,
                    score,
                    kind="corner_pair_support",
                    label="corner_junctions: aligned pair",
                    properties={
                        "pair_xy_px": [
                            [round(x1, 2), round(y1, 2)],
                            [round(x2, 2), round(y2, 2)],
                        ],
                        "distance_px": round(distance, 3),
                        "angle_deg": round(angle, 3),
                        "axis_bonus": round(axis_bonus, 4),
                        "pair_padding_px": pad,
                        "working_width": width,
                        "working_height": height,
                    },
                )
            )
    return _dedupe_cv_things(outputs, threshold=0.56, max_boxes=max_boxes)

def _quad_score(
    contour: np.ndarray,
    epsilon_fraction: float,
) -> tuple[float, int, float, float]:
    perimeter = max(float(cv2.arcLength(contour, True)), 1.0)
    approx = cv2.approxPolyDP(contour, epsilon_fraction * perimeter, True)
    vertices = len(approx)
    contour_area = max(float(cv2.contourArea(contour)), 1.0)
    hull_area = max(float(cv2.contourArea(cv2.convexHull(contour))), 1.0)
    x, y, box_width, box_height = cv2.boundingRect(contour)
    fill = contour_area / max(float(box_width * box_height), 1.0)
    solidity = contour_area / hull_area
    rectangularity = min(1.0, max(0.0, fill * 1.6))
    score = rectangularity * (1.0 if 4 <= vertices <= 8 else 0.72) * solidity
    return score, vertices, fill, solidity

def _quad_rectangularity(
    rgb: np.ndarray,
    *,
    working_width: int,
    max_boxes: int,
    shared_close_kernel: int = 0,
    shared_min_width_px: int = 0,
    shared_min_height_px: int = 0,
    shared_max_vertices: int = 0,
) -> list[PerceivedThing]:
    working = _working_rgb(rgb, working_width)
    outputs: list[PerceivedThing] = []
    for channel_name, channel in _gray_variants(working).items():
        for block, constant, morph_size, morph_iter in (
            (31, 5, 3, 1),
            (51, 9, 5, 1),
            (71, 12, 7, 2),
        ):
            if shared_close_kernel > 0:
                morph_size = shared_close_kernel
            binary = cv2.adaptiveThreshold(
                channel,
                255,
                cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY,
                block,
                constant,
            )
            kernel = cv2.getStructuringElement(
                cv2.MORPH_RECT,
                (morph_size, morph_size),
            )
            cleaned = cv2.morphologyEx(
                binary,
                cv2.MORPH_CLOSE,
                kernel,
                iterations=morph_iter,
            )
            cleaned = cv2.morphologyEx(
                cleaned,
                cv2.MORPH_OPEN,
                np.ones((3, 3), dtype=np.uint8),
                iterations=1,
            )
            contours, _ = cv2.findContours(
                cleaned,
                cv2.RETR_LIST,
                cv2.CHAIN_APPROX_SIMPLE,
            )
            for contour in contours:
                x, y, box_width, box_height = cv2.boundingRect(contour)
                area_fraction = box_width * box_height / max(float(cleaned.shape[0] * cleaned.shape[1]), 1.0)
                if (
                    area_fraction < 0.0025
                    or area_fraction > 0.30
                    or box_width < (shared_min_width_px or 20)
                    or box_height < (shared_min_height_px or 20)
                ):
                    continue
                score, vertices, fill, solidity = _quad_score(contour, 0.018)
                if score < 0.20 or vertices > (shared_max_vertices or 12):
                    continue
                bbox = _normalized_xywh(
                    x,
                    y,
                    box_width,
                    box_height,
                    cleaned.shape[1],
                    cleaned.shape[0],
                )
                outputs.append(
                    _cv_box_thing(
                        "quad_rectangularity",
                        len(outputs),
                        bbox,
                        0.35 + 0.50 * score,
                        kind="quadrilateral_fragment",
                        label="quad_rectangularity: loose quadrilateral",
                        properties={
                            "channel": channel_name,
                            "adaptive_block": block,
                            "adaptive_constant": constant,
                            "morph_kernel": morph_size,
                            "morph_iter": morph_iter,
                            "epsilon_fraction": 0.018,
                            "vertices": vertices,
                            "fill": round(fill, 4),
                            "solidity": round(solidity, 4),
                            "working_width": cleaned.shape[1],
                            "working_height": cleaned.shape[0],
                        },
                    )
                )
    return _dedupe_cv_things(outputs, threshold=0.90, max_boxes=max_boxes)

def _partial_contour(
    rgb: np.ndarray,
    *,
    working_width: int,
    max_boxes: int,
    shared_blur_kernel: int = 0,
    shared_close_kernel: int = 0,
    shared_min_width_px: int = 0,
    shared_min_height_px: int = 0,
) -> list[PerceivedThing]:
    working = _working_rgb(rgb, working_width)
    height, width = working.shape[:2]
    outputs: list[PerceivedThing] = []
    for channel_name, channel in _gray_variants(working).items():
        for low, high, blur in ((25, 75, 3), (45, 120, 5), (70, 170, 7)):
            if shared_blur_kernel > 0:
                blur = shared_blur_kernel
            edges = cv2.Canny(
                cv2.GaussianBlur(channel, (blur, blur), 0),
                low,
                high,
                L2gradient=True,
            )
            edges = cv2.morphologyEx(
                edges,
                cv2.MORPH_CLOSE,
                np.ones(
                    (shared_close_kernel or 3, shared_close_kernel or 3),
                    dtype=np.uint8,
                ),
                iterations=1,
            )
            contours, _ = cv2.findContours(
                edges,
                cv2.RETR_LIST,
                cv2.CHAIN_APPROX_NONE,
            )
            for contour in contours:
                x, y, box_width, box_height = cv2.boundingRect(contour)
                area_fraction = box_width * box_height / max(float(width * height), 1.0)
                if (
                    area_fraction < 0.0015
                    or area_fraction > 0.18
                    or box_width < (shared_min_width_px or 14)
                    or box_height < (shared_min_height_px or 14)
                ):
                    continue
                perimeter = max(float(cv2.arcLength(contour, False)), 1.0)
                contour_area = float(cv2.contourArea(contour))
                long_edge = max(box_width, box_height) / max(min(box_width, box_height), 1)
                compact = 1.0 - min(
                    1.0,
                    abs(math.log(max(long_edge, 1e-6))) / 3.5,
                )
                density = min(1.0, len(contour) / max(perimeter * 0.12, 1.0))
                confidence = (
                    0.25
                    + 0.35 * compact
                    + 0.25 * density
                    + 0.15 * min(1.0, contour_area / max(box_width * box_height, 1))
                )
                bbox = _normalized_xywh(x, y, box_width, box_height, width, height)
                outputs.append(
                    _cv_box_thing(
                        "partial_contour",
                        len(outputs),
                        bbox,
                        confidence,
                        kind="partial_edge_fragment",
                        label="partial_contour: edge fragment",
                        properties={
                            "channel": channel_name,
                            "canny": [low, high],
                            "blur": blur,
                            "close_kernel": shared_close_kernel or 3,
                            "close_iter": 1,
                            "partial": True,
                            "points": len(contour),
                            "long_edge_ratio": round(long_edge, 4),
                            "working_width": width,
                            "working_height": height,
                        },
                    )
                )
    return _dedupe_cv_things(outputs, threshold=0.88, max_boxes=max_boxes)

def _photometric_windows(
    rgb: np.ndarray,
    *,
    working_width: int,
    max_boxes: int,
) -> list[PerceivedThing]:
    working = _working_rgb(rgb, working_width)
    height, width = working.shape[:2]
    hsv = cv2.cvtColor(working, cv2.COLOR_RGB2HSV)
    gray = cv2.cvtColor(working, cv2.COLOR_RGB2GRAY)
    hue = hsv[..., 0]
    saturation = hsv[..., 1]
    value = hsv[..., 2]
    chromatic = (
        (hue >= 8)
        & (hue <= 35)
        & (saturation >= 45)
        & (saturation <= 205)
        & (value <= 190)
    )
    dark = (
        (hue >= 8)
        & (hue <= 35)
        & (saturation >= 55)
        & (value <= 125)
    )
    outputs: list[PerceivedThing] = []
    for width_fraction in (0.15, 0.22, 0.30, 0.38, 0.42):
        box_width = max(24, int(round(width * width_fraction)))
        for aspect in (0.75, 1.0, 1.25):
            box_height = max(24, int(round(box_width * aspect)))
            if box_width >= width or box_height >= height:
                continue
            step_x = max(12, int(round(box_width * 0.42)))
            step_y = max(12, int(round(box_height * 0.42)))
            for y in range(0, height - box_height + 1, step_y):
                for x in range(0, width - box_width + 1, step_x):
                    roi_chromatic = chromatic[y : y + box_height, x : x + box_width]
                    roi_dark = dark[y : y + box_height, x : x + box_width]
                    chromatic_fraction = float(np.mean(roi_chromatic))
                    dark_fraction = float(np.mean(roi_dark))
                    inside_mean = float(np.mean(gray[y : y + box_height, x : x + box_width]))
                    ring_x1 = max(0, x - box_width // 4)
                    ring_y1 = max(0, y - box_height // 4)
                    ring_x2 = min(width, x + box_width + box_width // 4)
                    ring_y2 = min(height, y + box_height + box_height // 4)
                    ring = gray[ring_y1:ring_y2, ring_x1:ring_x2]
                    if ring.size > roi_chromatic.size:
                        ring_mean = float(np.mean(ring))
                    else:
                        ring_mean = inside_mean
                    contrast = max(0.0, min(1.0, (ring_mean - inside_mean) / 90.0))
                    score = 0.45 * chromatic_fraction + 0.35 * dark_fraction + 0.20 * contrast
                    if score < 0.28:
                        continue
                    bbox = _normalized_xywh(x, y, box_width, box_height, width, height)
                    outputs.append(
                        _cv_box_thing(
                            "photometric_windows",
                            len(outputs),
                            bbox,
                            score,
                            kind="photometric_region",
                            label="photometric_windows: cardboard-like region",
                            properties={
                                "chromatic_fraction": round(chromatic_fraction, 4),
                                "dark_fraction": round(dark_fraction, 4),
                                "inside_ring_contrast": round(contrast, 4),
                                "window_width_fraction": width_fraction,
                                "aspect": aspect,
                                "working_width": width,
                                "working_height": height,
                            },
                        )
                    )
    return _dedupe_cv_things(outputs, threshold=0.60, max_boxes=max_boxes)

def _hough_fragments(
    rgb: np.ndarray,
    *,
    working_width: int,
    max_boxes: int,
    shared_blur_kernel: int = 0,
) -> list[PerceivedThing]:
    working = _working_rgb(rgb, working_width)
    height, width = working.shape[:2]
    gray = cv2.cvtColor(working, cv2.COLOR_RGB2GRAY)
    blur_kernel = shared_blur_kernel or 5
    gray = cv2.GaussianBlur(gray, (blur_kernel, blur_kernel), 0)
    edges = cv2.Canny(gray, 35, 105, L2gradient=True)
    lines = cv2.HoughLinesP(
        edges,
        1.0,
        np.pi / 180.0,
        threshold=30,
        minLineLength=34,
        maxLineGap=14,
    )
    if lines is None:
        return []
    outputs: list[PerceivedThing] = []
    padding = 12
    line_rows = lines[:, 0, :] if lines.ndim == 3 else lines
    for line in line_rows.tolist():
        x1, y1, x2, y2 = [int(value) for value in line]
        line_width = max(abs(x2 - x1), 1)
        line_height = max(abs(y2 - y1), 1)
        length = float(math.hypot(x2 - x1, y2 - y1))
        left = max(0, min(x1, x2) - padding)
        top = max(0, min(y1, y2) - padding)
        right = min(width - 1, max(x1, x2) + padding)
        bottom = min(height - 1, max(y1, y2) + padding)
        box_width = max(1, right - left + 1)
        box_height = max(1, bottom - top + 1)
        area_fraction = box_width * box_height / max(float(width * height), 1.0)
        if area_fraction < 0.001 or area_fraction > 0.20:
            continue
        support = float(np.count_nonzero(edges[top : bottom + 1, left : right + 1]))
        support = min(1.0, support / max(length * 0.75, 1.0))
        confidence = min(
            0.88,
            0.28
            + 0.30 * support
            + 0.30 * min(1.0, length / 220.0)
            + 0.12 * min(1.0, (line_width + line_height) / 220.0),
        )
        bbox = _normalized_xywh(left, top, box_width, box_height, width, height)
        angle = math.degrees(math.atan2(y2 - y1, x2 - x1))
        outputs.append(
            _cv_box_thing(
                "hough_fragments",
                len(outputs),
                bbox,
                confidence,
                kind="line_fragment",
                label="hough_fragments: partial line ROI",
                properties={
                    "line_xyxy_norm": [
                        round(x1 / max(width - 1, 1), 5),
                        round(y1 / max(height - 1, 1), 5),
                        round(x2 / max(width - 1, 1), 5),
                        round(y2 / max(height - 1, 1), 5),
                    ],
                    "length_px": round(length, 3),
                    "angle_deg": round(angle, 3),
                    "edge_support": round(support, 4),
                    "padding_px": padding,
                    "blur_kernel": blur_kernel,
                    "working_width": width,
                    "working_height": height,
                },
            )
        )
    return _dedupe_cv_things(outputs, threshold=0.72, max_boxes=max_boxes)

def _bbox_area(bbox: tuple[float, float, float, float]) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])

def _bbox_iou(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> float:
    x1 = max(left[0], right[0])
    y1 = max(left[1], right[1])
    x2 = min(left[2], right[2])
    y2 = min(left[3], right[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = _bbox_area(left) + _bbox_area(right) - intersection
    return intersection / union if union > 0.0 else 0.0

def _zone_from_bbox(bbox: tuple[float, float, float, float]) -> str:
    x1, y1, x2, y2 = bbox
    cx = (x1 + x2) / 2.0
    cy = (y1 + y2) / 2.0
    horizontal = "left" if cx < 0.45 else "right" if cx > 0.55 else "center"
    vertical = "near" if cy > 0.66 else "far" if cy < 0.33 else "mid"
    return f"{vertical}_{horizontal}"
