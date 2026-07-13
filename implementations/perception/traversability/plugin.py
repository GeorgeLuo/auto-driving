from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image

from autonomy.perception.interface import (
    PerceivedThing,
    PerceptionPluginContract,
    PerceptionPluginResult,
    PerceptionRequest,
    ViewLocation,
)
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID
from implementations.perception.components import (
    camera_component_id,
    camera_frame,
    camera_frame_error,
)
from implementations.perception.text import thing_line

from .model import (
    FloorPlaneConfig,
    estimate_floor_mask,
    make_overlay,
    project_topdown,
    render_occupancy,
    source_obstacle_hits,
)


FRONT_CAMERA_COMPONENT = camera_component_id(FRONT_CAMERA_SENSOR_ID)


class FloorPlanePlugin:
    """Estimate floor/traversability from the current front camera frame."""

    plugin_id = "floor-plane-v0"
    contract = PerceptionPluginContract(
        required_components=(FRONT_CAMERA_COMPONENT,),
        state_mode="stateless",
        artifact_policy="optional",
    )

    def __init__(
        self,
        *,
        write_artifacts: bool = True,
        config: FloorPlaneConfig | None = None,
    ) -> None:
        self.write_artifacts = bool(write_artifacts)
        self.config = config or FloorPlaneConfig()

    def reset(self) -> None:
        return None

    def describe_schema(self) -> dict[str, Any]:
        return {
            "plugin_id": self.plugin_id,
            "reads": ["front_camera RGB pixels"],
            "assumptions": [
                "lower-center image region is a reasonable floor color seed",
                "a sustained non-floor run encountered after visible floor is boundary evidence",
                "topdown_fov coordinates are approximate image-space projection, not calibrated metric geometry",
            ],
            "emits": [
                "signal id=floor_visible",
                "signal id=floor_boundary_available",
                "thing id=traversable_floor",
                "thing kind=floor_boundary for grouped first-hit boundary evidence",
            ],
            "artifacts": ["floor_mask", "floor_overlay", "topdown_rgb", "occupancy"],
        }

    def perceive(self, request: PerceptionRequest) -> PerceptionPluginResult:
        front = camera_frame(request, FRONT_CAMERA_SENSOR_ID)
        if front is None:
            return PerceptionPluginResult(
                status="unavailable",
                lines=(
                    "signal id=floor_visible value=false confidence=0.000 reason=no_front_camera",
                    "signal id=floor_boundary_available value=false confidence=0.000 reason=no_front_camera",
                ),
                observations={
                    self.plugin_id: {
                        "front_camera_available": False,
                        "input_error": camera_frame_error(request, FRONT_CAMERA_SENSOR_ID),
                    }
                },
                limits=("front camera image missing",),
            )

        try:
            analysis = _analyze_floor(
                rgb=front.rgb,
                source_path=front.source_path,
                output_dir=(request.output_dir / "floor_plane") if request.output_dir else None,
                config=self.config,
                write_artifacts=self.write_artifacts,
            )
        except Exception as exc:
            return PerceptionPluginResult(
                status="error",
                lines=(
                    "signal id=floor_visible value=false confidence=0.000 "
                    f"reason=analysis_failed error={type(exc).__name__}",
                    "signal id=floor_boundary_available value=false confidence=0.000 "
                    f"reason=analysis_failed error={type(exc).__name__}",
                ),
                observations={self.plugin_id: {"error": str(exc)}},
                limits=("floor plane analysis failed",),
                error=f"{type(exc).__name__}: {exc}",
            )

        floor_confidence = _floor_confidence(analysis["floor_fraction_roi"])
        boundaries = analysis["boundaries"]

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
                "boundary_hit_fraction_columns": analysis["boundary_hit_fraction_columns"],
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
                "signal id=floor_boundary_available "
                f"value={'true' if boundaries else 'false'} "
                f"confidence={_mean_boundary_confidence(boundaries):.3f} "
                f"boundary_count={len(boundaries)} "
                f"hit_fraction_columns={analysis['boundary_hit_fraction_columns']:.5f}"
            ),
            thing_line(floor),
        ]

        for index, boundary in enumerate(boundaries):
            thing = PerceivedThing(
                thing_id=f"floor_boundary_{index:03d}",
                kind="floor_boundary",
                label="first sustained non-floor boundary after visible floor",
                location=ViewLocation(
                    frame="image",
                    zone=_zone_from_bbox(boundary["bbox_xyxy_norm"]),
                    bbox_xyxy_norm=boundary["bbox_xyxy_norm"],
                ),
                confidence=boundary["confidence"],
                properties={
                    "evidence": "first_non_floor_after_visible_floor",
                    "width_fraction": boundary["width_fraction"],
                    "hit_pixel_count": boundary["hit_pixel_count"],
                    "floor_fraction_roi": analysis["floor_fraction_roi"],
                },
            )
            things.append(thing)
            lines.append(thing_line(thing))

        return PerceptionPluginResult(
            lines=tuple(lines),
            things=tuple(things),
            observations={
                self.plugin_id: {
                    "image_width_px": analysis["width"],
                    "image_height_px": analysis["height"],
                    "floor_fraction_roi": analysis["floor_fraction_roi"],
                    "occupied_fraction_roi": analysis["occupied_fraction_roi"],
                    "boundary_hit_fraction_columns": analysis["boundary_hit_fraction_columns"],
                    "boundaries": boundaries,
                    "artifact_writes_enabled": self.write_artifacts,
                }
            },
            artifacts=analysis["artifacts"],
            limits=(
                "floor model is color-seeded from the current frame",
                "floor boundaries may represent objects, walls, shadows, or floor-color discontinuities",
                "topdown_fov is approximate and uncalibrated",
            ),
        )


def _analyze_floor(
    *,
    rgb: np.ndarray,
    source_path: Path | None,
    output_dir: Path | None,
    config: FloorPlaneConfig,
    write_artifacts: bool,
) -> dict[str, Any]:
    image = Image.fromarray(rgb, mode="RGB")
    normalized_rgb = rgb.astype(np.float32) / 255.0
    height, width = normalized_rgb.shape[:2]

    floor_mask, roi_mask, model_info = estimate_floor_mask(normalized_rgb, config)
    occupied_mask = roi_mask & ~floor_mask
    boundary_hits = source_obstacle_hits(floor_mask, config)
    boundaries = _boundary_components(boundary_hits, config)
    roi_pixels = max(int(roi_mask.sum()), 1)
    source_columns = int(np.count_nonzero((boundary_hits & roi_mask).any(axis=0)))
    source_width = max(1, int(np.count_nonzero(roi_mask.any(axis=0))))

    artifacts: dict[str, str] = {}
    if write_artifacts and output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        overlay = make_overlay(image, floor_mask, occupied_mask, config)
        overlay_array = np.asarray(overlay).copy()
        overlay_array[boundary_hits] = np.array([255, 225, 30], dtype=np.uint8)
        overlay = Image.fromarray(overlay_array, mode="RGB")
        topdown_rgb, occupancy = project_topdown(normalized_rgb, floor_mask, occupied_mask, config)
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
            "image": str(source_path) if source_path is not None else None,
            "width": width,
            "height": height,
            "floor_fraction_roi": float((floor_mask & roi_mask).sum() / roi_pixels),
            "occupied_fraction_roi": float((occupied_mask & roi_mask).sum() / roi_pixels),
            "boundary_hit_fraction_columns": float(source_columns / source_width),
            "boundaries": boundaries,
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
        "boundary_hit_fraction_columns": round(float(source_columns / source_width), 5),
        "boundaries": boundaries,
        "artifacts": artifacts,
    }


def _boundary_components(mask: np.ndarray, config: FloorPlaneConfig) -> list[dict[str, Any]]:
    if not mask.any():
        return []
    height, width = mask.shape
    count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(
        mask.astype(np.uint8), connectivity=8
    )
    minimum_width = max(2, int(round(width * config.min_boundary_width_ratio)))
    boundaries: list[dict[str, Any]] = []
    for label in range(1, count):
        x = int(stats[label, cv2.CC_STAT_LEFT])
        y = int(stats[label, cv2.CC_STAT_TOP])
        component_width = int(stats[label, cv2.CC_STAT_WIDTH])
        component_height = int(stats[label, cv2.CC_STAT_HEIGHT])
        area = int(stats[label, cv2.CC_STAT_AREA])
        if component_width < minimum_width:
            continue
        width_fraction = component_width / max(width, 1)
        confidence = min(1.0, 0.35 + width_fraction / max(config.min_boundary_width_ratio * 8.0, 1e-6))
        boundaries.append({
            "bbox_xyxy_norm": (
                round(x / max(width - 1, 1), 4),
                round(y / max(height - 1, 1), 4),
                round((x + component_width - 1) / max(width - 1, 1), 4),
                round((y + component_height - 1) / max(height - 1, 1), 4),
            ),
            "width_fraction": round(width_fraction, 5),
            "hit_pixel_count": area,
            "confidence": round(float(confidence), 5),
        })
    boundaries.sort(key=lambda item: (item["width_fraction"], item["hit_pixel_count"]), reverse=True)
    return boundaries[:8]


def _floor_confidence(floor_fraction: float) -> float:
    return round(float(min(1.0, max(0.0, floor_fraction / 0.70))), 5)


def _mean_boundary_confidence(boundaries: list[dict[str, Any]]) -> float:
    if not boundaries:
        return 0.0
    return float(sum(item["confidence"] for item in boundaries) / len(boundaries))


def _zone_from_bbox(bbox: tuple[float, float, float, float] | None) -> str:
    if bbox is None:
        return "unknown"
    x1, y1, x2, y2 = bbox
    cx = (x1 + x2) / 2.0
    cy = (y1 + y2) / 2.0
    horizontal = "left" if cx < 0.45 else "right" if cx > 0.55 else "center"
    vertical = "near" if cy > 0.66 else "far" if cy < 0.33 else "mid"
    return f"{vertical}_{horizontal}"
