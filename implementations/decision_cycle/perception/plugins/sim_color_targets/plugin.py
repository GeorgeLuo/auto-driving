from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import cv2
import numpy as np
from PIL import Image

from autonomy.fields import FieldError, require_number

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
from implementations.decision_cycle.perception.feeds.camera import CameraFrame, FRONT_CAMERA_RGB_INPUT


@dataclass(frozen=True)
class ColorRegion:
    present: bool
    bbox_xyxy_norm: tuple[float, float, float, float] | None = None
    pixel_fraction: float = 0.0
    confidence: float = 0.0


class SimColorTargetsPlugin:
    """Debug-only color detector for Chase sim front camera frames."""

    plugin_id = "sim_color_targets"
    contract = PerceptionPluginContract(
        inputs=(FRONT_CAMERA_RGB_INPUT,),
        description="Detect simulator control targets with known debug colors.",
        assumptions=(
            "Chase evaders are red or pink in the front camera",
            "Chase obstruction controls are low-saturation gray or white regions",
            "left, center, and right use image-space bounding-box centers",
        ),
        emits=(
            "signals evader_in_sight and obstruction_in_sight",
            "spatial evidence for present color targets",
        ),
        limitations=(
            "color thresholds are simulator debug heuristics",
            "no semantic recognition or depth estimate",
        ),
    )

    def __init__(
        self,
        *,
        thumbnail_width: int = 320,
        min_evader_fraction: float = 0.0005,
        min_obstruction_fraction: float = 0.01,
        evader_red_minimum: float = 0.45,
        evader_red_lead: float = 0.12,
        evader_red_green_ratio: float = 1.25,
        evader_red_blue_ratio: float = 1.10,
        neutral_chroma_maximum: float = 0.055,
        neutral_luma_minimum: float = 0.18,
        neutral_luma_maximum: float = 0.92,
        beige_red_blue_minimum: float = 0.035,
        beige_row_minimum: float = 0.35,
        dark_luma_maximum: float = 0.12,
    ) -> None:
        self.thumbnail_width = max(80, int(thumbnail_width))
        self.min_evader_fraction = max(0.0, float(min_evader_fraction))
        self.min_obstruction_fraction = max(0.0, float(min_obstruction_fraction))
        self.evader_red_minimum = require_number(evader_red_minimum, field_name="evader_red_minimum", bounds=(0, 1))
        self.evader_red_lead = require_number(evader_red_lead, field_name="evader_red_lead", bounds=(0, 1))
        self.evader_red_green_ratio = require_number(evader_red_green_ratio, field_name="evader_red_green_ratio", bounds=(0, 1e6))
        self.evader_red_blue_ratio = require_number(evader_red_blue_ratio, field_name="evader_red_blue_ratio", bounds=(0, 1e6))
        self.neutral_luma_minimum, self.neutral_luma_maximum = _ordered_pair(
            "neutral_luma_minimum", neutral_luma_minimum,
            "neutral_luma_maximum", neutral_luma_maximum,
        )
        self.neutral_chroma_maximum = require_number(
            neutral_chroma_maximum, field_name="neutral_chroma_maximum", bounds=(0, 1),
        )
        self.beige_red_blue_minimum = require_number(
            beige_red_blue_minimum, field_name="beige_red_blue_minimum", bounds=(0, 1),
        )
        self.beige_row_minimum = require_number(beige_row_minimum, field_name="beige_row_minimum", bounds=(0, 1))
        self.dark_luma_maximum = require_number(dark_luma_maximum, field_name="dark_luma_maximum", bounds=(0, 1))

    def perceive(self, inputs: PerceptionPluginInputs) -> PerceptionEvidenceBatch:
        frame = inputs.require("frame", CameraFrame)
        rgb = _resize_rgb(frame.rgb, self.thumbnail_width)
        evader = _detect_region(rgb, lambda image: _evader_mask(image, self), self.min_evader_fraction)
        obstruction = _detect_region(
            rgb, lambda image: _obstruction_mask(image, self), self.min_obstruction_fraction,
        )

        things: list[PerceivedThing] = []

        if evader.present and evader.bbox_xyxy_norm is not None:
            thing = _target_thing(
                thing_id="evader",
                kind="evader",
                label="evader color target",
                region=evader,
                evidence="red_color_threshold",
            )
            things.append(thing)

        if obstruction.present and obstruction.bbox_xyxy_norm is not None:
            thing = _target_thing(
                thing_id="obstruction",
                kind="obstruction_evidence",
                label="obstruction color target",
                region=obstruction,
                evidence="neutral_gray_threshold",
            )
            things.append(thing)

        return PerceptionEvidenceBatch(
            signals=(
                _region_signal("evader_in_sight", evader),
                _region_signal("obstruction_in_sight", obstruction),
            ),
            things=tuple(things),
            measurements={
                "thumbnail_width": int(rgb.shape[1]),
                "thumbnail_height": int(rgb.shape[0]),
                "evader": _region_dict(evader),
                "obstruction": _region_dict(obstruction),
            },
        )


def _resize_rgb(rgb: np.ndarray, thumbnail_width: int) -> np.ndarray:
    image = Image.fromarray(rgb, mode="RGB")
    if image.width != thumbnail_width:
        height = max(1, round(image.height * (thumbnail_width / image.width)))
        image = image.resize((thumbnail_width, height), Image.Resampling.BILINEAR)
    return np.asarray(image).astype(np.float32) / 255.0


def _ordered_pair(low_name: str, low: object, high_name: str, high: object) -> tuple[float, float]:
    low_value = require_number(low, field_name=low_name, bounds=(0, 1))
    high_value = require_number(high, field_name=high_name, bounds=(0, 1))
    if low_value > high_value:
        raise FieldError(low_name, f"must be <= {high_name}")
    return low_value, high_value


def _evader_mask(rgb: np.ndarray, plugin: SimColorTargetsPlugin) -> np.ndarray:
    r = rgb[:, :, 0]
    g = rgb[:, :, 1]
    b = rgb[:, :, 2]
    return (
        (r > plugin.evader_red_minimum)
        & ((r - np.maximum(g, b)) > plugin.evader_red_lead)
        & (r > g * plugin.evader_red_green_ratio)
        & (r > b * plugin.evader_red_blue_ratio)
    )


def _obstruction_mask(rgb: np.ndarray, plugin: SimColorTargetsPlugin) -> np.ndarray:
    r = rgb[:, :, 0]
    g = rgb[:, :, 1]
    b = rgb[:, :, 2]
    brightness = rgb.mean(axis=2)
    chroma = rgb.max(axis=2) - rgb.min(axis=2)
    height = rgb.shape[0]
    yy = np.arange(height)[:, None] / max(height - 1, 1)

    neutral_gray = (
        (chroma < plugin.neutral_chroma_maximum)
        & (brightness > plugin.neutral_luma_minimum)
        & (brightness < plugin.neutral_luma_maximum)
    )
    beige_floor = (
        (r > g)
        & (g > b)
        & ((r - b) > plugin.beige_red_blue_minimum)
        & (yy > plugin.beige_row_minimum)
    )
    very_dark = brightness < plugin.dark_luma_maximum
    return neutral_gray & ~beige_floor & ~very_dark


def _detect_region(
    rgb: np.ndarray,
    mask_fn: Callable[[np.ndarray], np.ndarray],
    min_fraction: float,
) -> ColorRegion:
    mask = mask_fn(rgb)
    height, width = mask.shape[:2]
    component = _largest_component(mask)
    if component is None:
        return ColorRegion(False)

    x1, y1, x2, y2, area = component
    fraction = area / max(1, width * height)
    if fraction < min_fraction:
        return ColorRegion(False, pixel_fraction=round(float(fraction), 5))

    confidence = min(1.0, fraction / max(min_fraction * 4.0, 1e-6))
    bbox = (
        round(x1 / max(width - 1, 1), 4),
        round(y1 / max(height - 1, 1), 4),
        round(x2 / max(width - 1, 1), 4),
        round(y2 / max(height - 1, 1), 4),
    )
    return ColorRegion(
        present=True,
        bbox_xyxy_norm=bbox,
        pixel_fraction=round(float(fraction), 5),
        confidence=round(float(confidence), 5),
    )


def _largest_component(mask: np.ndarray) -> tuple[int, int, int, int, int] | None:
    """Largest 8-connected component. Equal areas keep the first label in raster order."""

    count, labels, stats, _centroids = cv2.connectedComponentsWithStats(
        mask.astype(np.uint8), connectivity=8,
    )
    if count == 1:
        return None
    areas = stats[1:, cv2.CC_STAT_AREA]
    tied = np.flatnonzero(areas == areas.max()) + 1
    if len(tied) == 1:
        label = int(tied[0])
    else:
        is_tied = np.zeros(count, dtype=bool)
        is_tied[tied] = True
        label = int(labels.ravel()[np.argmax(is_tied[labels.ravel()])])
    x, y, width, height, area = (int(value) for value in stats[label])
    return x, y, x + width - 1, y + height - 1, area


def _region_signal(signal_id: str, region: ColorRegion) -> PerceptionSignal:
    return PerceptionSignal(
        signal_id=signal_id,
        value=region.present,
        confidence=region.confidence,
        properties={
            "direction": _horizontal_direction(region.bbox_xyxy_norm),
            "bbox_xyxy_norm": region.bbox_xyxy_norm,
            "pixel_fraction": region.pixel_fraction,
        },
    )


def _target_thing(
    *,
    thing_id: str,
    kind: str,
    label: str,
    region: ColorRegion,
    evidence: str,
) -> PerceivedThing:
    direction = _horizontal_direction(region.bbox_xyxy_norm)
    return PerceivedThing(
        thing_id=thing_id,
        kind=kind,
        label=label,
        location=ViewLocation(
            frame="image",
            zone=_zone_from_bbox(region.bbox_xyxy_norm),
            bbox_xyxy_norm=region.bbox_xyxy_norm,
        ),
        confidence=region.confidence,
        properties={
            "direction": direction,
            "evidence": evidence,
            "pixel_fraction": region.pixel_fraction,
        },
    )


def _region_dict(region: ColorRegion) -> dict[str, Any]:
    return {
        "present": region.present,
        "bbox_xyxy_norm": region.bbox_xyxy_norm,
        "pixel_fraction": region.pixel_fraction,
        "confidence": region.confidence,
        "direction": _horizontal_direction(region.bbox_xyxy_norm),
        "zone": _zone_from_bbox(region.bbox_xyxy_norm),
    }


def _horizontal_direction(bbox: tuple[float, float, float, float] | None) -> str:
    if bbox is None:
        return "unknown"
    x1, _, x2, _ = bbox
    center = (x1 + x2) / 2.0
    if center < 0.45:
        return "left"
    if center > 0.55:
        return "right"
    return "center"


def _zone_from_bbox(bbox: tuple[float, float, float, float] | None) -> str:
    if bbox is None:
        return "unknown"
    _, y1, _, y2 = bbox
    cy = (y1 + y2) / 2.0
    vertical = "near" if cy > 0.66 else "far" if cy < 0.33 else "mid"
    return f"{vertical}_{_horizontal_direction(bbox)}"
