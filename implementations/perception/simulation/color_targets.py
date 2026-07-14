from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np
from PIL import Image

from autonomy.perception import (
    PerceivedThing,
    PerceptionEvidenceBatch,
    PerceptionPluginContract,
    PerceptionPluginInputs,
    PerceptionSignal,
    ViewLocation,
)
from implementations.perception.components import CameraFrame, FRONT_CAMERA_RGB_INPUT


@dataclass(frozen=True)
class ColorRegion:
    present: bool
    bbox_xyxy_norm: tuple[float, float, float, float] | None = None
    pixel_fraction: float = 0.0
    confidence: float = 0.0


class SimColorTargetsPlugin:
    """Debug-only color detector for Chase sim front camera frames."""

    plugin_id = "sim-color-targets-v0"
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
    ) -> None:
        self.thumbnail_width = max(80, int(thumbnail_width))
        self.min_evader_fraction = max(0.0, float(min_evader_fraction))
        self.min_obstruction_fraction = max(0.0, float(min_obstruction_fraction))

    def perceive(self, inputs: PerceptionPluginInputs) -> PerceptionEvidenceBatch:
        frame = inputs.require("frame", CameraFrame)
        rgb = _resize_rgb(frame.rgb, self.thumbnail_width)
        evader = _detect_region(rgb, _evader_mask, self.min_evader_fraction)
        obstruction = _detect_region(rgb, _obstruction_mask, self.min_obstruction_fraction)

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


def _evader_mask(rgb: np.ndarray) -> np.ndarray:
    r = rgb[:, :, 0]
    g = rgb[:, :, 1]
    b = rgb[:, :, 2]
    return (
        (r > 0.45)
        & ((r - np.maximum(g, b)) > 0.12)
        & (r > g * 1.25)
        & (r > b * 1.10)
    )


def _obstruction_mask(rgb: np.ndarray) -> np.ndarray:
    r = rgb[:, :, 0]
    g = rgb[:, :, 1]
    b = rgb[:, :, 2]
    brightness = rgb.mean(axis=2)
    chroma = rgb.max(axis=2) - rgb.min(axis=2)
    height = rgb.shape[0]
    yy = np.arange(height)[:, None] / max(height - 1, 1)

    neutral_gray = (chroma < 0.055) & (brightness > 0.18) & (brightness < 0.92)
    beige_floor = (r > g) & (g > b) & ((r - b) > 0.035) & (yy > 0.35)
    very_dark = brightness < 0.12
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
    height, width = mask.shape[:2]
    visited = np.zeros((height, width), dtype=bool)
    best: tuple[int, int, int, int, int] | None = None

    ys, xs = np.nonzero(mask)
    for start_x, start_y in zip(xs.tolist(), ys.tolist(), strict=False):
        if visited[start_y, start_x] or not mask[start_y, start_x]:
            continue
        x1 = x2 = start_x
        y1 = y2 = start_y
        area = 0
        queue: deque[tuple[int, int]] = deque([(start_x, start_y)])
        visited[start_y, start_x] = True

        while queue:
            x, y = queue.popleft()
            area += 1
            x1 = min(x1, x)
            x2 = max(x2, x)
            y1 = min(y1, y)
            y2 = max(y2, y)

            for ny in range(max(0, y - 1), min(height, y + 2)):
                for nx in range(max(0, x - 1), min(width, x + 2)):
                    if visited[ny, nx] or not mask[ny, nx]:
                        continue
                    visited[ny, nx] = True
                    queue.append((nx, ny))

        if best is None or area > best[4]:
            best = (x1, y1, x2, y2, area)

    return best


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
