from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image

from autonomy.perception.interface import PerceivedThing, PerceptionRequest, ViewLocation
from implementations.perception.chain import PerceptionPluginResult
from implementations.perception.text import thing_line
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID


@dataclass(frozen=True)
class ColorRegion:
    present: bool
    bbox_xyxy_norm: tuple[float, float, float, float] | None = None
    pixel_fraction: float = 0.0
    confidence: float = 0.0


class SimColorTargetsPlugin:
    """Debug-only color detector for Chase sim front camera frames."""

    plugin_id = "sim-color-targets-v0"

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

    def describe_schema(self) -> dict[str, Any]:
        return {
            "plugin_id": self.plugin_id,
            "reads": ["front_camera RGB pixels"],
            "assumptions": [
                "Chase sim evader is red/pink in the front camera",
                "Chase sim obstruction surfaces are low-saturation gray/white regions",
                "left/right/center is based on bbox center in image coordinates",
            ],
            "emits": [
                "signal id=evader_in_sight",
                "signal id=obstruction_in_sight",
                "thing id=evader when red target pixels are present",
                "thing id=obstruction when gray/white obstruction pixels are present",
            ],
        }

    def perceive(self, request: PerceptionRequest) -> PerceptionPluginResult:
        front = request.snapshot.readings.get(FRONT_CAMERA_SENSOR_ID)
        if front is None or front.path is None:
            return PerceptionPluginResult(
                lines=(
                    "signal id=evader_in_sight value=false confidence=0.000 reason=no_front_camera",
                    "signal id=obstruction_in_sight value=false confidence=0.000 reason=no_front_camera",
                ),
                observations={self.plugin_id: {"front_camera_available": False}},
                limits=("front camera image missing",),
            )

        image_path = Path(front.path)
        rgb = _load_rgb(image_path, self.thumbnail_width)
        evader = _detect_region(rgb, _evader_mask, self.min_evader_fraction)
        obstruction = _detect_region(rgb, _obstruction_mask, self.min_obstruction_fraction)

        things: list[PerceivedThing] = []
        lines = [
            _signal_line("evader_in_sight", evader),
            _signal_line("obstruction_in_sight", obstruction),
        ]

        if evader.present and evader.bbox_xyxy_norm is not None:
            thing = _target_thing(
                thing_id="evader",
                kind="evader",
                label="evader color target",
                region=evader,
                evidence="red_color_threshold",
            )
            things.append(thing)
            lines.append(thing_line(thing))

        if obstruction.present and obstruction.bbox_xyxy_norm is not None:
            thing = _target_thing(
                thing_id="obstruction",
                kind="obstruction_evidence",
                label="obstruction color target",
                region=obstruction,
                evidence="neutral_gray_threshold",
            )
            things.append(thing)
            lines.append(thing_line(thing))

        return PerceptionPluginResult(
            lines=tuple(lines),
            things=tuple(things),
            observations={
                self.plugin_id: {
                    "thumbnail_width": int(rgb.shape[1]),
                    "thumbnail_height": int(rgb.shape[0]),
                    "evader": _region_dict(evader),
                    "obstruction": _region_dict(obstruction),
                }
            },
            limits=(
                "color thresholds are simulator/debug heuristics",
                "no semantic recognition or depth estimate",
            ),
        )


def _load_rgb(path: Path, thumbnail_width: int) -> np.ndarray:
    image = Image.open(path).convert("RGB")
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


def _signal_line(signal_id: str, region: ColorRegion) -> str:
    direction = _horizontal_direction(region.bbox_xyxy_norm)
    bbox = "none" if region.bbox_xyxy_norm is None else ",".join(
        f"{value:.4f}" for value in region.bbox_xyxy_norm
    )
    return (
        f"signal id={signal_id} value={'true' if region.present else 'false'} "
        f"direction={direction} bbox_xyxy_norm={bbox} "
        f"confidence={region.confidence:.3f} pixel_fraction={region.pixel_fraction:.5f}"
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
