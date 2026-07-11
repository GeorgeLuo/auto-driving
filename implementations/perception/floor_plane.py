from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image

from autonomy.perception.interface import PerceivedThing, PerceptionRequest, ViewLocation
from implementations.perception.chain import PerceptionPluginResult
from implementations.perception.text import thing_line
from autonomy.perception.traversability.floor_plane import (
    FloorPlaneConfig,
    estimate_floor_mask,
    make_overlay,
    project_topdown,
    render_occupancy,
)
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID


class FloorPlanePlugin:
    """Estimate floor/traversability from the current front camera frame."""

    plugin_id = "floor-plane-v0"

    def __init__(
        self,
        *,
        min_obstruction_fraction: float = 0.035,
        write_artifacts: bool = True,
        config: FloorPlaneConfig | None = None,
    ) -> None:
        self.min_obstruction_fraction = max(0.0, float(min_obstruction_fraction))
        self.write_artifacts = bool(write_artifacts)
        self.config = config or FloorPlaneConfig()

    def describe_schema(self) -> dict[str, Any]:
        return {
            "plugin_id": self.plugin_id,
            "reads": ["front_camera RGB pixels"],
            "assumptions": [
                "lower-center image region is a reasonable floor color seed",
                "non-floor pixels inside the forward floor ROI are obstruction evidence",
                "topdown_fov coordinates are approximate image-space projection, not calibrated metric geometry",
            ],
            "emits": [
                "signal id=floor_visible",
                "signal id=possible_obstruction",
                "thing id=traversable_floor",
                "thing id=possible_obstruction when enough non-floor ROI is present",
            ],
            "artifacts": ["floor_mask", "floor_overlay", "topdown_rgb", "occupancy"],
        }

    def perceive(self, request: PerceptionRequest) -> PerceptionPluginResult:
        front = request.snapshot.readings.get(FRONT_CAMERA_SENSOR_ID)
        if front is None or front.path is None:
            return PerceptionPluginResult(
                lines=(
                    "signal id=floor_visible value=false confidence=0.000 reason=no_front_camera",
                    "signal id=possible_obstruction value=false confidence=0.000 reason=no_front_camera",
                ),
                observations={self.plugin_id: {"front_camera_available": False}},
                limits=("front camera image missing",),
            )

        image_path = Path(front.path)
        try:
            analysis = _analyze_floor(
                image_path=image_path,
                output_dir=(request.output_dir / "floor_plane") if request.output_dir else None,
                config=self.config,
                write_artifacts=self.write_artifacts,
            )
        except Exception as exc:
            return PerceptionPluginResult(
                lines=(
                    f"signal id=floor_visible value=false confidence=0.000 reason=analysis_failed error={type(exc).__name__}",
                    f"signal id=possible_obstruction value=false confidence=0.000 reason=analysis_failed error={type(exc).__name__}",
                ),
                observations={self.plugin_id: {"error": str(exc)}},
                limits=("floor plane analysis failed",),
            )

        floor_confidence = _floor_confidence(analysis["floor_fraction_roi"])
        obstruction_present = analysis["occupied_component_fraction_roi"] >= self.min_obstruction_fraction
        obstruction_confidence = _obstruction_confidence(
            analysis["occupied_component_fraction_roi"],
            self.min_obstruction_fraction,
        )

        floor = PerceivedThing(
            thing_id="traversable_floor",
            kind="surface",
            label="traversable floor estimate",
            location=ViewLocation(
                frame="topdown_fov",
                zone="visible_floor",
                bbox_xyxy_norm=(0.0, 0.0, 1.0, 1.0),
            ),
            confidence=floor_confidence,
            properties={
                "evidence": "color_floor_model",
                "floor_fraction_roi": analysis["floor_fraction_roi"],
                "occupied_fraction_roi": analysis["occupied_fraction_roi"],
                "occupied_component_fraction_roi": analysis["occupied_component_fraction_roi"],
            },
        )

        things = [floor]
        lines = [
            (
                "signal id=floor_visible "
                f"value={'true' if analysis['floor_fraction_roi'] > 0.25 else 'false'} "
                f"confidence={floor_confidence:.3f} "
                f"floor_fraction_roi={analysis['floor_fraction_roi']:.5f}"
            ),
            (
                "signal id=possible_obstruction "
                f"value={'true' if obstruction_present else 'false'} "
                f"confidence={obstruction_confidence:.3f} "
                f"occupied_fraction_roi={analysis['occupied_fraction_roi']:.5f} "
                f"occupied_component_fraction_roi={analysis['occupied_component_fraction_roi']:.5f} "
                f"bbox_xyxy_norm={_bbox_text(analysis['occupied_bbox_norm'])}"
            ),
            thing_line(floor),
        ]

        if obstruction_present and analysis["occupied_bbox_norm"] is not None:
            obstruction = PerceivedThing(
                thing_id="possible_obstruction",
                kind="obstruction_evidence",
                label="non-floor region in forward floor ROI",
                location=ViewLocation(
                    frame="image",
                    zone=_zone_from_bbox(analysis["occupied_bbox_norm"]),
                    bbox_xyxy_norm=analysis["occupied_bbox_norm"],
                ),
                confidence=obstruction_confidence,
                properties={
                    "evidence": "non_floor_in_floor_roi",
                    "occupied_fraction_roi": analysis["occupied_fraction_roi"],
                    "occupied_component_fraction_roi": analysis["occupied_component_fraction_roi"],
                    "floor_fraction_roi": analysis["floor_fraction_roi"],
                },
            )
            things.append(obstruction)
            lines.append(thing_line(obstruction))

        return PerceptionPluginResult(
            lines=tuple(lines),
            things=tuple(things),
            observations={
                self.plugin_id: {
                    "image_width_px": analysis["width"],
                    "image_height_px": analysis["height"],
                    "floor_fraction_roi": analysis["floor_fraction_roi"],
                    "occupied_fraction_roi": analysis["occupied_fraction_roi"],
                    "occupied_component_fraction_roi": analysis["occupied_component_fraction_roi"],
                    "occupied_bbox_xyxy_norm": analysis["occupied_bbox_norm"],
                    "artifact_writes_enabled": self.write_artifacts,
                }
            },
            artifacts=analysis["artifacts"],
            limits=(
                "floor model is color-seeded from the current frame",
                "topdown_fov is approximate and uncalibrated",
            ),
        )


def _analyze_floor(
    *,
    image_path: Path,
    output_dir: Path | None,
    config: FloorPlaneConfig,
    write_artifacts: bool,
) -> dict[str, Any]:
    image = Image.open(image_path).convert("RGB")
    rgb = np.asarray(image).astype(np.float32) / 255.0
    height, width = rgb.shape[:2]

    floor_mask, roi_mask, model_info = estimate_floor_mask(rgb, config)
    occupied_mask = roi_mask & ~floor_mask
    occupied_component_mask, occupied_component_area = _largest_component(occupied_mask)
    roi_pixels = max(int(roi_mask.sum()), 1)

    artifacts: dict[str, str] = {}
    if write_artifacts and output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        overlay = make_overlay(image, floor_mask, occupied_mask, config)
        topdown_rgb, occupancy = project_topdown(rgb, floor_mask, occupied_mask, config)
        occupancy_img = render_occupancy(occupancy)

        files = {
            "floor_mask": output_dir / "floor_mask.png",
            "floor_overlay": output_dir / "overlay.png",
            "topdown_rgb": output_dir / "topdown_rgb.jpg",
            "occupancy": output_dir / "occupancy.png",
            "summary": output_dir / "summary.json",
        }
        Image.fromarray((floor_mask.astype(np.uint8) * 255), mode="L").save(files["floor_mask"])
        overlay.save(files["floor_overlay"])
        Image.fromarray(topdown_rgb).save(files["topdown_rgb"], quality=92)
        occupancy_img.save(files["occupancy"])
        summary = {
            "image": str(image_path),
            "width": width,
            "height": height,
            "floor_fraction_roi": float((floor_mask & roi_mask).sum() / roi_pixels),
            "occupied_fraction_roi": float((occupied_mask & roi_mask).sum() / roi_pixels),
            "occupied_component_fraction_roi": float(occupied_component_area / roi_pixels),
            "occupied_bbox_xyxy_norm": _mask_bbox_norm(occupied_component_mask),
            "config": asdict(config) | {"floor_model": model_info},
            "artifacts": {name: str(path) for name, path in files.items()},
        }
        files["summary"].write_text(json.dumps(summary, indent=2), encoding="utf-8")
        artifacts = {name: str(path) for name, path in files.items()}

    return {
        "width": width,
        "height": height,
        "floor_fraction_roi": round(float((floor_mask & roi_mask).sum() / roi_pixels), 5),
        "occupied_fraction_roi": round(float((occupied_mask & roi_mask).sum() / roi_pixels), 5),
        "occupied_component_fraction_roi": round(float(occupied_component_area / roi_pixels), 5),
        "occupied_bbox_norm": _mask_bbox_norm(occupied_component_mask),
        "artifacts": artifacts,
    }


def _largest_component(mask: np.ndarray) -> tuple[np.ndarray, int]:
    if not mask.any():
        return np.zeros_like(mask, dtype=bool), 0

    count, labels, stats, _centroids = cv2.connectedComponentsWithStats(
        mask.astype(np.uint8),
        connectivity=8,
    )
    if count <= 1:
        return np.zeros_like(mask, dtype=bool), 0

    areas = stats[1:, cv2.CC_STAT_AREA]
    best_label = int(np.argmax(areas)) + 1
    best_area = int(stats[best_label, cv2.CC_STAT_AREA])
    return labels == best_label, best_area


def _mask_bbox_norm(mask: np.ndarray) -> tuple[float, float, float, float] | None:
    ys, xs = np.nonzero(mask)
    if len(xs) == 0 or len(ys) == 0:
        return None
    height, width = mask.shape[:2]
    return (
        round(float(xs.min()) / max(width - 1, 1), 4),
        round(float(ys.min()) / max(height - 1, 1), 4),
        round(float(xs.max()) / max(width - 1, 1), 4),
        round(float(ys.max()) / max(height - 1, 1), 4),
    )


def _floor_confidence(floor_fraction: float) -> float:
    return round(float(min(1.0, max(0.0, floor_fraction / 0.70))), 5)


def _obstruction_confidence(occupied_fraction: float, min_fraction: float) -> float:
    if occupied_fraction <= 0:
        return 0.0
    return round(float(min(1.0, occupied_fraction / max(min_fraction * 3.0, 1e-6))), 5)


def _bbox_text(bbox: tuple[float, float, float, float] | None) -> str:
    if bbox is None:
        return "none"
    return ",".join(f"{value:.4f}" for value in bbox)


def _zone_from_bbox(bbox: tuple[float, float, float, float] | None) -> str:
    if bbox is None:
        return "unknown"
    x1, y1, x2, y2 = bbox
    cx = (x1 + x2) / 2.0
    cy = (y1 + y2) / 2.0
    horizontal = "left" if cx < 0.45 else "right" if cx > 0.55 else "center"
    vertical = "near" if cy > 0.66 else "far" if cy < 0.33 else "mid"
    return f"{vertical}_{horizontal}"
