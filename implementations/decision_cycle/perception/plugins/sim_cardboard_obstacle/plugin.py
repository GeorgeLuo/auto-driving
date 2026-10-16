"""Bounding boxes for the Chase simulator's orange-brown cardboard obstacles."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from autonomy.decision_cycle.perception.evidence.values import (
    PerceivedThing,
    PerceptionEvidenceBatch,
    PerceptionSignal,
    ViewLocation,
)
from autonomy.decision_cycle.perception.plugin import (
    PerceptionPluginContract,
    PerceptionPluginInputs,
)
from implementations.decision_cycle.perception.feeds.camera import (
    CameraFrame,
    FRONT_CAMERA_RGB_INPUT,
)


@dataclass(frozen=True)
class _Obstacle:
    present: bool
    bbox_xyxy_norm: tuple[float, float, float, float] | None = None
    area_fraction: float = 0.0
    solidity: float = 0.0


class SimCardboardObstaclePlugin:
    """Find simulator cardboard blocks and ignore beige floor and gray sky."""

    plugin_id = "sim_cardboard_obstacle"
    contract = PerceptionPluginContract(
        inputs=(FRONT_CAMERA_RGB_INPUT,),
        description=(
            "One image-space bounding box per visible Chase cardboard obstacle."
        ),
        assumptions=(
            "obstacles are saturated orange-brown cardboard blocks",
            "the floor is lighter beige and the sky is near gray",
            "the contact shadow is darker and less chromatic than the block",
        ),
        emits=(
            "signal obstacle_in_sight",
            "one obstruction_evidence box per visible cardboard region",
        ),
        limitations=(
            "color thresholds are for this simulator cardboard, not real scenes",
            "the box is axis-aligned, so a sloped edge includes some background",
            "overlapping silhouettes need a visible ground-edge step to separate",
            "no depth estimate and no identity across frames",
        ),
    )

    def __init__(
        self,
        *,
        working_width: int = 640,
        min_area_fraction: float = 0.001,
        max_area_fraction: float = 0.9,
        close_px: int = 5,
        open_px: int = 3,
    ) -> None:
        self.working_width = max(80, int(working_width))
        self.min_area_fraction = max(0.0, min(1.0, float(min_area_fraction)))
        self.max_area_fraction = max(self.min_area_fraction, min(1.0, float(max_area_fraction)))
        self.close_px = max(0, int(close_px))
        self.open_px = max(0, int(open_px))

    def perceive(self, inputs: PerceptionPluginInputs) -> PerceptionEvidenceBatch:
        frame = inputs.require("frame", CameraFrame)
        working = _resize_rgb(frame.rgb, self.working_width)
        obstacles = _detect(
            working,
            min_area_fraction=self.min_area_fraction,
            max_area_fraction=self.max_area_fraction,
            close_px=self.close_px,
            open_px=self.open_px,
        )
        # Retain the primary-box fields for readers of the original detector.
        primary = obstacles[0] if obstacles else _Obstacle(False)
        return PerceptionEvidenceBatch(
            signals=(_sight_signal(primary, len(obstacles)),),
            things=tuple(_obstacle_thing(obstacle, index) for index, obstacle in enumerate(obstacles)),
            measurements={
                "working_width": int(working.shape[1]),
                "working_height": int(working.shape[0]),
                "obstacle": _obstacle_dict(primary),
                "obstacles": [_obstacle_dict(obstacle) for obstacle in obstacles],
            },
        )


def _resize_rgb(rgb: np.ndarray, working_width: int) -> np.ndarray:
    height, width = rgb.shape[:2]
    if width <= working_width:
        return rgb
    scale = working_width / width
    resized = cv2.resize(
        rgb,
        (working_width, max(1, int(round(height * scale)))),
        interpolation=cv2.INTER_AREA,
    )
    return np.ascontiguousarray(resized)


def _detect(
    rgb: np.ndarray,
    *,
    min_area_fraction: float,
    max_area_fraction: float,
    close_px: int,
    open_px: int,
) -> tuple[_Obstacle, ...]:
    mask = _cardboard_mask(rgb)
    mask = _refine_mask(mask, close_px=close_px, open_px=open_px)
    height, width = mask.shape[:2]
    count, labels, stats, _centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    image_area = max(1, width * height)
    obstacles: list[_Obstacle] = []
    for label in range(1, count):
        x, y, box_width, box_height, area = (int(value) for value in stats[label])
        if area / image_area < min_area_fraction:
            continue
        component = labels[y:y + box_height, x:x + box_width] == label
        for offset, region in _visible_regions(component, min_area_fraction * image_area):
            area = int(region.sum())
            fraction = area / image_area
            if not min_area_fraction <= fraction <= max_area_fraction:
                continue
            rows, columns = np.nonzero(region)
            x1, x2 = x + offset + int(columns.min()), x + offset + int(columns.max())
            y1, y2 = y + int(rows.min()), y + int(rows.max())
            bbox = (
                round(x1 / max(width - 1, 1), 4),
                round(y1 / max(height - 1, 1), 4),
                round(x2 / max(width - 1, 1), 4),
                round(y2 / max(height - 1, 1), 4),
            )
            solidity = area / max(1, (x2 - x1 + 1) * (y2 - y1 + 1))
            obstacles.append(_Obstacle(
                present=True,
                bbox_xyxy_norm=bbox,
                area_fraction=round(float(fraction), 5),
                solidity=round(float(min(1.0, solidity)), 5),
            ))
    return tuple(sorted(obstacles, key=lambda item: (-item.area_fraction, item.bbox_xyxy_norm)))


def _visible_regions(component: np.ndarray, minimum_area: float) -> list[tuple[int, np.ndarray]]:
    """Separate touching boxes at steps in their visible ground-contact edge.

    Box faces at different depths can touch in the color mask. Their lower
    silhouettes have abrupt steps, while a single box's base slopes gradually.
    Reject cuts leaving narrow edge fragments so rounded corners and cropped
    faces remain one detection.
    """

    height, width = component.shape
    minimum_width = max(5, int(round(height * 0.1)))
    if width < 2 * minimum_width:
        return [(0, component)]
    bottom = height - 1 - component[::-1].argmax(axis=0).astype(np.float32)
    bottom = cv2.medianBlur(bottom[None, :], 5).ravel()
    steps = np.abs(bottom[4:] - bottom[:-4])
    candidates = np.flatnonzero(steps >= max(3.0, height * 0.04)) + 2
    groups: list[list[int]] = []
    for column in candidates:
        column = int(column)
        if groups and column == groups[-1][-1] + 1:
            groups[-1].append(column)
        else:
            groups.append([column])
    boundaries = [0]
    for group in groups:
        cut = max(group, key=lambda column: steps[column - 2])
        if cut - boundaries[-1] < minimum_width or width - cut < minimum_width:
            continue
        if component[:, boundaries[-1]:cut].sum() < minimum_area:
            continue
        if component[:, cut:].sum() < minimum_area:
            continue
        boundaries.append(cut)
    boundaries.append(width)
    return [(left, component[:, left:right]) for left, right in zip(boundaries, boundaries[1:])]


def _cardboard_mask(rgb: np.ndarray) -> np.ndarray:
    image = rgb.astype(np.float32) / 255.0
    red = image[:, :, 0]
    green = image[:, :, 1]
    blue = image[:, :, 2]
    chroma = image.max(axis=2) - image.min(axis=2)
    brightness = image.mean(axis=2)
    cardboard = (
        (red > 0.28)
        & (red < 0.86)
        & (green > 0.12)
        & (green < 0.66)
        & (blue < 0.48)
        & ((red - green) > 0.08)
        & ((green - blue) > 0.04)
        & (chroma > 0.20)
        & (brightness < 0.72)
        & (brightness > 0.16)
    )
    return cardboard.astype(np.uint8)


def _refine_mask(mask: np.ndarray, *, close_px: int, open_px: int) -> np.ndarray:
    refined = mask
    if close_px >= 3:
        refined = cv2.morphologyEx(refined, cv2.MORPH_CLOSE, _kernel(close_px))
    if open_px >= 3:
        refined = cv2.morphologyEx(refined, cv2.MORPH_OPEN, _kernel(open_px))
    return refined


def _kernel(size: int) -> np.ndarray:
    odd = size if size % 2 == 1 else size + 1
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (odd, odd))


def _obstacle_thing(obstacle: _Obstacle, index: int) -> PerceivedThing:
    bbox = obstacle.bbox_xyxy_norm
    return PerceivedThing(
        thing_id="cardboard_obstacle" if index == 0 else f"cardboard_obstacle_{index:03d}",
        kind="obstruction_evidence",
        label="cardboard obstacle",
        location=ViewLocation(
            frame="image",
            zone=_zone(bbox),
            bbox_xyxy_norm=bbox,
        ),
        confidence=obstacle.solidity,
        properties={
            "evidence": "cardboard_color",
            "area_fraction": obstacle.area_fraction,
            "solidity": obstacle.solidity,
            "direction": _direction(bbox),
        },
    )


def _sight_signal(obstacle: _Obstacle, count: int) -> PerceptionSignal:
    return PerceptionSignal(
        signal_id="obstacle_in_sight",
        value=obstacle.present,
        confidence=obstacle.solidity if obstacle.present else 0.0,
        properties={
            "obstacle_count": count,
            "bbox_xyxy_norm": obstacle.bbox_xyxy_norm,
            "area_fraction": obstacle.area_fraction,
            "direction": _direction(obstacle.bbox_xyxy_norm),
        },
    )


def _obstacle_dict(obstacle: _Obstacle) -> dict[str, Any]:
    return {
        "present": obstacle.present,
        "bbox_xyxy_norm": obstacle.bbox_xyxy_norm,
        "area_fraction": obstacle.area_fraction,
        "solidity": obstacle.solidity,
        "direction": _direction(obstacle.bbox_xyxy_norm),
        "zone": _zone(obstacle.bbox_xyxy_norm),
    }


def _direction(bbox: tuple[float, float, float, float] | None) -> str:
    if bbox is None:
        return "unknown"
    center = (bbox[0] + bbox[2]) / 2.0
    if center < 0.4:
        return "left"
    if center > 0.6:
        return "right"
    return "center"


def _zone(bbox: tuple[float, float, float, float] | None) -> str:
    if bbox is None:
        return "unknown"
    center_y = (bbox[1] + bbox[3]) / 2.0
    vertical = "near" if center_y > 0.66 else "far" if center_y < 0.33 else "mid"
    return f"{vertical}_{_direction(bbox)}"
