from __future__ import annotations

import json
from pathlib import Path
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
from implementations.decision_cycle.perception.shared.regions.detection import detect_regions


class ClassicalRegionPlugin:
    """Generate generic coherent-color regions with core OpenCV operations."""

    plugin_id = "classical_regions"
    contract = PerceptionPluginContract(
        inputs=(FRONT_CAMERA_RGB_INPUT,),
        description="Generate coherent-color region proposals with OpenCV.",
        assumptions=(
            "locally coherent color is useful current-frame structure evidence",
        ),
        emits=(
            "signal classical_regions_available",
            "spatial region_proposal evidence for accepted color components",
        ),
        limitations=(
            "regions are color components, not semantic objects",
            "lighting can split one surface or merge adjacent surfaces",
            "single-frame regions do not estimate depth or persistence",
        ),
        diagnostic_artifacts=(
            "classical_regions",
            "classical_smoothed",
            "classical_summary",
        ),
    )

    def __init__(
        self,
        *,
        working_width: int = 320,
        spatial_radius: int = 8,
        color_radius: int = 18,
        min_area_fraction: float = 0.003,
        max_area_fraction: float = 0.65,
        max_regions: int = 32,
    ) -> None:
        self.working_width = max(160, int(working_width))
        self.spatial_radius = max(1, int(spatial_radius))
        self.color_radius = max(1, int(color_radius))
        self.min_area_fraction = max(0.0, min(1.0, float(min_area_fraction)))
        self.max_area_fraction = max(self.min_area_fraction, min(1.0, float(max_area_fraction)))
        self.max_regions = max(1, int(max_regions))

    def perceive(self, inputs: PerceptionPluginInputs) -> PerceptionEvidenceBatch:
        frame = inputs.require("frame", CameraFrame)
        proposals, diagnostic = detect_regions(
            frame.rgb,
            working_width=self.working_width,
            spatial_radius=self.spatial_radius,
            color_radius=self.color_radius,
            min_area_fraction=self.min_area_fraction,
            max_area_fraction=self.max_area_fraction,
            max_regions=self.max_regions,
        )
        things = tuple(_proposal_thing(index, proposal) for index, proposal in enumerate(proposals))
        if inputs.diagnostics.enabled:
            output_dir = inputs.diagnostics.directory
            assert output_dir is not None
            artifacts = _write_artifacts(
                frame.rgb,
                diagnostic["smoothed_rgb"],
                proposals,
                output_dir,
            )
            inputs.diagnostics.register(artifacts)

        return PerceptionEvidenceBatch(
            signals=(
                PerceptionSignal(
                    "classical_regions_available",
                    bool(things),
                    _mean_confidence(things),
                    {
                        "components": diagnostic["component_count"],
                        "regions": len(things),
                    },
                ),
            ),
            things=things,
            measurements={
                "working_width": diagnostic["working_width"],
                "working_height": diagnostic["working_height"],
                "component_count": diagnostic["component_count"],
                "region_count": len(things),
            },
        )


def _proposal_thing(index: int, proposal: dict[str, Any]) -> PerceivedThing:
    return PerceivedThing(
        thing_id=f"classical_region_{index:03d}",
        kind="region_proposal",
        label="coherent color region",
        location=ViewLocation(
            frame="image",
            zone=_zone(proposal["centroid"]),
            bbox_xyxy_norm=proposal["bbox"],
        ),
        confidence=proposal["confidence"],
        properties={
            "evidence": "classical_color_component",
            "area_fraction": proposal["area_fraction"],
            "centroid_xy_norm": proposal["centroid"],
            "contour_xy_norm": proposal["contour"],
            "touches_lower_image": proposal["touches_lower_image"],
            "color_coherence": proposal["color_coherence"],
            "solidity": proposal["solidity"],
        },
    )


def _zone(centroid: tuple[float, float]) -> str:
    x, y = centroid
    horizontal = "left" if x < 0.4 else "right" if x > 0.6 else "center"
    vertical = "near" if y > 0.66 else "far" if y < 0.33 else "mid"
    return f"{vertical}_{horizontal}"


def _mean_confidence(things: tuple[PerceivedThing, ...]) -> float:
    if not things:
        return 0.0
    return float(sum(thing.confidence for thing in things) / len(things))


def _write_artifacts(
    rgb: np.ndarray,
    smoothed_rgb: np.ndarray,
    proposals: list[dict[str, Any]],
    output_dir: Path,
) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    height, width = rgb.shape[:2]
    overlay = rgb.copy()
    palette = np.array([
        [38, 166, 91],
        [43, 116, 189],
        [218, 135, 39],
        [172, 74, 184],
        [201, 72, 74],
    ], dtype=np.uint8)
    for index, proposal in enumerate(proposals):
        mask = cv2.resize(
            proposal["mask"].astype(np.uint8), (width, height), interpolation=cv2.INTER_NEAREST
        ).astype(bool)
        color = palette[index % len(palette)]
        overlay[mask] = (0.55 * overlay[mask] + 0.45 * color).astype(np.uint8)
    overlay_path = output_dir / "regions.png"
    smoothed_path = output_dir / "smoothed.png"
    summary_path = output_dir / "summary.json"
    cv2.imwrite(str(overlay_path), cv2.cvtColor(overlay, cv2.COLOR_RGB2BGR))
    cv2.imwrite(str(smoothed_path), cv2.cvtColor(smoothed_rgb, cv2.COLOR_RGB2BGR))
    summary_path.write_text(
        json.dumps({
            "regions": [
                {key: value for key, value in proposal.items() if key != "mask"}
                for proposal in proposals
            ],
        }, indent=2),
        encoding="utf-8",
    )
    return {
        "classical_regions": str(overlay_path),
        "classical_smoothed": str(smoothed_path),
        "classical_summary": str(summary_path),
    }
