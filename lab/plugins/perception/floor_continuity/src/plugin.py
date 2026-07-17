from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from autonomy.perception import (
    PerceivedThing,
    PerceptionEvidenceBatch,
    PerceptionPluginContract,
    PerceptionPluginInputs,
    PerceptionSignal,
    ViewLocation,
)
from implementations.perception.components import CameraFrame, FRONT_CAMERA_RGB_INPUT

from .model import FloorContinuityAnalysis, FloorContinuityConfig, analyze_floor_continuity


class FloorContinuityPlugin:
    """Emit current-frame floor support and first-interruption evidence."""

    plugin_id = "floor-continuity-v1"
    contract = PerceptionPluginContract(
        inputs=(FRONT_CAMERA_RGB_INPUT,),
        state_mode="stateless",
        description=(
            "Estimate bottom-connected floor support and generic first interruptions "
            "using current-frame color, texture, and gradient cues."
        ),
        assumptions=(
            "several lower-center image patches contain representative visible floor",
            "floor support is image-connected to the lower field of view",
            "a sustained interruption after supported floor is useful generic evidence",
        ),
        emits=(
            "signals floor_visible and floor_boundary_available",
            "image-space visible floor support",
            "image-space floor_boundary evidence with cue measurements",
        ),
        limitations=(
            "floor support is heuristic and does not establish traversability",
            "boundaries may be objects, walls, shadows, surface changes, or errors",
            "single-frame evidence does not establish identity, distance, or persistence",
        ),
        diagnostic_artifacts=(
            "floor_mask",
            "boundary_mask",
            "overlay",
            "summary",
        ),
    )

    def __init__(self, **config: Any) -> None:
        self.config = FloorContinuityConfig(**config)

    def perceive(self, inputs: PerceptionPluginInputs) -> PerceptionEvidenceBatch:
        frame = inputs.require("frame", CameraFrame)
        analysis = analyze_floor_continuity(frame.rgb, self.config)
        floor_confidence = float(analysis.measurements["floor_confidence"])
        boundary_confidence = float(analysis.measurements["boundary_confidence"])
        things: list[PerceivedThing] = []

        if analysis.floor is not None:
            things.append(
                PerceivedThing(
                    thing_id="visible_floor_support",
                    kind="surface",
                    label="bottom-connected floor support",
                    location=ViewLocation(
                        frame="image",
                        zone="visible_floor",
                        bbox_xyxy_norm=analysis.floor["bbox_xyxy_norm"],
                        polygon_xy_norm=analysis.floor["polygon_xy_norm"],
                    ),
                    confidence=floor_confidence,
                    properties={
                        "evidence": "multi_cue_bottom_connected_floor",
                        "floor_fraction_roi": analysis.measurements["floor_fraction_roi"],
                        "center_floor_support": analysis.measurements["center_floor_support"],
                        "seed_quality": analysis.measurements["seed_quality"],
                    },
                )
            )

        for index, boundary in enumerate(analysis.boundaries):
            things.append(_boundary_thing(index, boundary, analysis))

        if inputs.diagnostics.enabled:
            output_dir = inputs.diagnostics.directory
            assert output_dir is not None
            inputs.diagnostics.register(
                _write_diagnostics(
                    frame.rgb,
                    analysis,
                    self.config,
                    output_dir,
                    inputs.frame_id,
                )
            )

        floor_visible = (
            float(analysis.measurements["floor_fraction_roi"])
            >= self.config.minimum_floor_fraction
        )
        return PerceptionEvidenceBatch(
            signals=(
                PerceptionSignal(
                    "floor_visible",
                    floor_visible,
                    floor_confidence,
                    {
                        "floor_fraction_roi": analysis.measurements["floor_fraction_roi"],
                        "center_floor_support": analysis.measurements["center_floor_support"],
                    },
                ),
                PerceptionSignal(
                    "floor_boundary_available",
                    bool(analysis.boundaries),
                    boundary_confidence,
                    {"boundary_count": len(analysis.boundaries)},
                ),
            ),
            things=tuple(things),
            measurements=dict(analysis.measurements),
        )


def _boundary_thing(
    index: int,
    boundary: dict[str, Any],
    analysis: FloorContinuityAnalysis,
) -> PerceivedThing:
    return PerceivedThing(
        thing_id=f"floor_boundary_{index:03d}",
        kind="floor_boundary",
        label="supported floor interruption",
        location=ViewLocation(
            frame="image",
            zone=_zone(boundary["centroid_xy_norm"]),
            bbox_xyxy_norm=boundary["bbox_xyxy_norm"],
            polygon_xy_norm=boundary["polygon_xy_norm"],
        ),
        confidence=float(boundary["confidence"]),
        properties={
            "evidence": "multi_cue_floor_continuity_interruption",
            "floor_support_below": boundary["floor_support_below"],
            "width_fraction": boundary["width_fraction"],
            "edge_agreement": boundary["edge_agreement"],
            "vertical_consistency": boundary["vertical_consistency"],
            "color_discontinuity": boundary["color_discontinuity"],
            "texture_discontinuity": boundary["texture_discontinuity"],
            "cue_agreement": boundary["cue_agreement"],
            "ambiguity": boundary["ambiguity"],
            "seed_quality": analysis.measurements["seed_quality"],
            "blur_score": analysis.measurements["blur_score"],
            "processing_width_px": analysis.working_width,
            "processing_height_px": analysis.working_height,
        },
    )


def _zone(centroid: tuple[float, float]) -> str:
    x, y = centroid
    horizontal = "left" if x < 0.4 else "right" if x > 0.6 else "center"
    vertical = "near" if y > 0.66 else "far" if y < 0.33 else "mid"
    return f"{vertical}_{horizontal}"


def _write_diagnostics(
    source_rgb: np.ndarray,
    analysis: FloorContinuityAnalysis,
    config: FloorContinuityConfig,
    output_dir: Path,
    frame_id: str,
) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    source_size = (analysis.source_width, analysis.source_height)
    floor_mask = cv2.resize(
        analysis.floor_mask.astype(np.uint8) * 255,
        source_size,
        interpolation=cv2.INTER_NEAREST,
    )
    boundary_mask = cv2.resize(
        analysis.boundary_mask.astype(np.uint8) * 255,
        source_size,
        interpolation=cv2.INTER_NEAREST,
    )
    overlay = np.array(source_rgb, copy=True)
    floor_pixels = floor_mask > 0
    overlay[floor_pixels] = (
        0.72 * overlay[floor_pixels]
        + 0.28 * np.array([42, 190, 92], dtype=np.float32)
    ).astype(np.uint8)
    boundary_pixels = boundary_mask > 0
    overlay[boundary_pixels] = np.array([238, 67, 52], dtype=np.uint8)
    for boundary in analysis.boundaries:
        x1, y1, x2, y2 = boundary["bbox_xyxy_norm"]
        cv2.rectangle(
            overlay,
            (
                int(round(x1 * max(analysis.source_width - 1, 1))),
                int(round(y1 * max(analysis.source_height - 1, 1))),
            ),
            (
                int(round(x2 * max(analysis.source_width - 1, 1))),
                int(round(y2 * max(analysis.source_height - 1, 1))),
            ),
            (255, 218, 35),
            2,
        )

    floor_path = output_dir / "floor_mask.png"
    boundary_path = output_dir / "boundary_mask.png"
    overlay_path = output_dir / "overlay.png"
    summary_path = output_dir / "summary.json"
    cv2.imwrite(str(floor_path), floor_mask)
    cv2.imwrite(str(boundary_path), boundary_mask)
    cv2.imwrite(str(overlay_path), cv2.cvtColor(overlay, cv2.COLOR_RGB2BGR))
    summary_path.write_text(
        json.dumps(
            {
                "frame_id": frame_id,
                "plugin_id": FloorContinuityPlugin.plugin_id,
                "config": asdict(config),
                "measurements": analysis.measurements,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return {
        "floor_mask": str(floor_path),
        "boundary_mask": str(boundary_path),
        "overlay": str(overlay_path),
        "summary": str(summary_path),
    }
