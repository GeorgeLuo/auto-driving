from __future__ import annotations

import json
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


class ClassicalRegionPlugin:
    """Generate generic coherent-color regions with core OpenCV operations."""

    plugin_id = "classical-regions-v0"
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
        proposals, diagnostic = _detect_regions(
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


def _detect_regions(
    rgb: np.ndarray,
    *,
    working_width: int,
    spatial_radius: int,
    color_radius: int,
    min_area_fraction: float,
    max_area_fraction: float,
    max_regions: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source_height, source_width = rgb.shape[:2]
    scale = min(1.0, working_width / source_width)
    width = max(1, int(round(source_width * scale)))
    height = max(1, int(round(source_height * scale)))
    working_rgb = cv2.resize(rgb, (width, height), interpolation=cv2.INTER_AREA) if scale < 1 else rgb.copy()
    working_bgr = cv2.cvtColor(working_rgb, cv2.COLOR_RGB2BGR)
    smoothed_bgr = cv2.pyrMeanShiftFiltering(
        working_bgr,
        sp=spatial_radius,
        sr=color_radius,
        maxLevel=1,
    )
    smoothed_rgb = cv2.cvtColor(smoothed_bgr, cv2.COLOR_BGR2RGB)
    lab = cv2.cvtColor(smoothed_bgr, cv2.COLOR_BGR2LAB)
    quantized = (
        (lab[..., 0].astype(np.int32) // 24) * 169
        + (lab[..., 1].astype(np.int32) // 20) * 13
        + (lab[..., 2].astype(np.int32) // 20)
    )
    image_area = max(1, width * height)
    min_area = max(4, int(round(image_area * min_area_fraction)))
    max_area = max(min_area, int(round(image_area * max_area_fraction)))
    kernel = np.ones((3, 3), dtype=np.uint8)
    proposals: list[dict[str, Any]] = []
    component_count = 0

    values, counts = np.unique(quantized, return_counts=True)
    for value, count in zip(values, counts):
        if count < min_area:
            continue
        mask = (quantized == value).astype(np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        label_count, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
        component_count += max(0, label_count - 1)
        for label in range(1, label_count):
            area = int(stats[label, cv2.CC_STAT_AREA])
            if area < min_area or area > max_area:
                continue
            x = int(stats[label, cv2.CC_STAT_LEFT])
            y = int(stats[label, cv2.CC_STAT_TOP])
            component_width = int(stats[label, cv2.CC_STAT_WIDTH])
            component_height = int(stats[label, cv2.CC_STAT_HEIGHT])
            if component_width < 4 or component_height < 4:
                continue
            component_mask = labels == label
            contours, _ = cv2.findContours(
                component_mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )
            if not contours:
                continue
            contour = max(contours, key=cv2.contourArea)
            hull_area = max(float(cv2.contourArea(cv2.convexHull(contour))), 1.0)
            solidity = min(1.0, area / hull_area)
            pixels = lab[component_mask].astype(np.float32)
            channel_std = float(np.mean(np.std(pixels, axis=0))) if len(pixels) else 255.0
            coherence = max(0.0, min(1.0, 1.0 - channel_std / 32.0))
            support = min(1.0, area / max(min_area * 8.0, 1.0))
            confidence = round(0.45 * coherence + 0.35 * solidity + 0.20 * support, 5)
            contour_points = _normalized_contour(contour, width, height)
            centroid = centroids[label]
            proposals.append({
                "mask": component_mask,
                "area_fraction": round(area / image_area, 6),
                "bbox": (
                    round(x / max(width - 1, 1), 5),
                    round(y / max(height - 1, 1), 5),
                    round((x + component_width - 1) / max(width - 1, 1), 5),
                    round((y + component_height - 1) / max(height - 1, 1), 5),
                ),
                "centroid": (
                    round(float(centroid[0]) / max(width - 1, 1), 5),
                    round(float(centroid[1]) / max(height - 1, 1), 5),
                ),
                "contour": contour_points,
                "confidence": confidence,
                "color_coherence": round(coherence, 5),
                "solidity": round(solidity, 5),
                "touches_lower_image": bool(y + component_height - 1 >= height * 0.85),
            })

    proposals.sort(key=lambda item: (item["confidence"], item["area_fraction"]), reverse=True)
    return proposals[:max_regions], {
        "working_width": width,
        "working_height": height,
        "component_count": component_count,
        "smoothed_rgb": smoothed_rgb,
    }


def _normalized_contour(contour: np.ndarray, width: int, height: int) -> list[list[float]]:
    epsilon = max(1.0, cv2.arcLength(contour, True) * 0.01)
    points = cv2.approxPolyDP(contour, epsilon, True).reshape(-1, 2)
    if len(points) > 64:
        points = points[np.linspace(0, len(points) - 1, 64, dtype=int)]
    return [
        [
            round(float(x) / max(width - 1, 1), 5),
            round(float(y) / max(height - 1, 1), 5),
        ]
        for x, y in points
    ]


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
