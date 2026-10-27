from __future__ import annotations

from typing import Any


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

from .model import FloorContinuityAnalysis, FloorContinuityConfig, analyze_floor_continuity


class FloorContinuityPlugin:
    """Emit current-frame floor support and first-interruption evidence."""

    plugin_id = "floor_continuity"
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
