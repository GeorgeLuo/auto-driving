"""One bounding box for the Chase simulator's orange-brown cardboard obstacle."""

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
    """Find the simulator cardboard block and ignore beige floor and gray sky."""

    plugin_id = "sim_cardboard_obstacle"
    contract = PerceptionPluginContract(
        inputs=(FRONT_CAMERA_RGB_INPUT,),
        description=(
            "One image-space bounding box for the Chase cardboard obstacle."
        ),
        assumptions=(
            "the obstacle is the saturated orange-brown cardboard block",
            "the floor is lighter beige and the sky is near gray",
            "the contact shadow is darker and less chromatic than the block",
        ),
        emits=(
            "signal obstacle_in_sight",
            "one obstruction_evidence box when the block is in frame",
        ),
        limitations=(
            "color thresholds are for this simulator cardboard, not real scenes",
            "the box is axis-aligned, so a sloped edge includes some background",
            "no depth estimate and no identity across frames",
        ),
    )

    def __init__(
        self,
        *,
        working_width: int = 640,
        min_area_fraction: float = 0.008,
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
        obstacle = _detect(
            working,
            min_area_fraction=self.min_area_fraction,
            max_area_fraction=self.max_area_fraction,
            close_px=self.close_px,
            open_px=self.open_px,
        )
        things: tuple[PerceivedThing, ...] = ()
        if obstacle.present and obstacle.bbox_xyxy_norm is not None:
            things = (_obstacle_thing(obstacle),)
        return PerceptionEvidenceBatch(
            signals=(_sight_signal(obstacle),),
            things=things,
            measurements={
                "working_width": int(working.shape[1]),
                "working_height": int(working.shape[0]),
                "obstacle": _obstacle_dict(obstacle),
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
) -> _Obstacle:
    mask = _cardboard_mask(rgb)
    mask = _refine_mask(mask, close_px=close_px, open_px=open_px)
    height, width = mask.shape[:2]
    count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    image_area = max(1, width * height)
    best_area = 0
    best_label = 0
    for label in range(1, count):
        area = int(stats[label, cv2.CC_STAT_AREA])
        if area > best_area:
            best_area = area
            best_label = label
    fraction = best_area / image_area
    if best_label == 0 or fraction < min_area_fraction or fraction > max_area_fraction:
        return _Obstacle(False, area_fraction=round(float(fraction), 5))

    x = int(stats[best_label, cv2.CC_STAT_LEFT])
    y = int(stats[best_label, cv2.CC_STAT_TOP])
    box_width = int(stats[best_label, cv2.CC_STAT_WIDTH])
    box_height = int(stats[best_label, cv2.CC_STAT_HEIGHT])
    bbox = (
        round(x / max(width - 1, 1), 4),
        round(y / max(height - 1, 1), 4),
        round((x + box_width - 1) / max(width - 1, 1), 4),
        round((y + box_height - 1) / max(height - 1, 1), 4),
    )
    solidity = best_area / max(1, box_width * box_height)
    return _Obstacle(
        present=True,
        bbox_xyxy_norm=bbox,
        area_fraction=round(float(fraction), 5),
        solidity=round(float(min(1.0, solidity)), 5),
    )


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


def _obstacle_thing(obstacle: _Obstacle) -> PerceivedThing:
    bbox = obstacle.bbox_xyxy_norm
    return PerceivedThing(
        thing_id="cardboard_obstacle",
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


def _sight_signal(obstacle: _Obstacle) -> PerceptionSignal:
    return PerceptionSignal(
        signal_id="obstacle_in_sight",
        value=obstacle.present,
        confidence=obstacle.solidity if obstacle.present else 0.0,
        properties={
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
