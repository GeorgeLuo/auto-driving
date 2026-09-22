from __future__ import annotations

from dataclasses import fields
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from autonomy.perception import (
    PerceivedThing,
    PerceptionEvidenceBatch,
    PerceptionPluginContract,
    PerceptionPluginInputs,
    ViewLocation,
)
from implementations.perception.components import CameraFrame, FRONT_CAMERA_RGB_INPUT
from lab.plugins.perception.classical_regions.src.plugin import _detect_regions
from lab.plugins.perception.floor_continuity.src.model import (
    FloorContinuityConfig,
    analyze_floor_continuity,
)
from lab.plugins.perception.multi_obstruction_tracks.src.plugin import (
    MultiObstructionTracksPlugin,
    _clamp,
    _zone,
)

from .issue219_cues import (
    _adaptive_contours,
    _bbox_area,
    _bbox_iou,
    _canny_morph,
    _corner_junctions,
    _hough_fragments,
    _partial_contour,
    _photometric_windows,
    _quad_rectangularity,
)
from .geometry import (
    FIXED_JEV_PROMPT,
    GeometryHypothesis,
    GeometryProposal,
    build_jev_request,
    compare_selectors,
    cluster_proposals,
    make_hypotheses,
    proposal_from_record,
    select_jev_hypotheses,
    source_balanced_budget,
)


class CompositeBoxFusionPlugin(MultiObstructionTracksPlugin):
    """Fuse shared CV substrategies before the existing obstruction tracker.

    The tracker and emitted ``obstacle`` records are inherited from
    ``MultiObstructionTracksPlugin``.  Issue 219 contributes current-frame
    proposal generators only; line and junction detections remain supporting
    measurements and cannot create a standalone object cluster.
    """

    plugin_id = "composite-box-fusion-v1"
    contract = PerceptionPluginContract(
        inputs=(FRONT_CAMERA_RGB_INPUT,),
        state_mode="windowed",
        description=(
            "Combine edge, partial-face, photometric, floor, line, and junction "
            "evidence, then reuse multi-obstruction association and flow support."
        ),
        assumptions=(
            "all proposals are image-space evidence rather than semantic truth",
            "line and junction cues support nearby proposals but do not connect objects",
            "raw RGB remains available for photometric and floor features",
        ),
        emits=(
            "signal multi_obstruction_tracks_available",
            "multiple image-space obstacle records with bounded temporal ids",
            "per-source composite proposal measurements",
        ),
        limitations=(
            "proposal geometry is intentionally unselected until the Step 3 matrix",
            "floor boundaries can be shadows or surface changes",
            "tracking remains local to one reset-bounded run",
        ),
        diagnostic_artifacts=("track_summary", "edge_summary", "composite_summary"),
    )

    _STRATEGY_FAMILIES = {
        "edge_contours": "edge_family",
        "partial_faces": "edge_family",
        "photometric_regions": "photometric_family",
        "classical_regions": "photometric_family",
        "photometric_windows": "photometric_family",
        "floor_context": "floor_family",
    }
    _DEFAULT_STRATEGIES = (
        "edge_contours",
        "partial_faces",
        "photometric_regions",
        "floor_context",
        "line_junction_support",
    )

    def __init__(
        self,
        *,
        enabled_substrategies: list[str] | tuple[str, ...] | None = None,
        issue_working_width: int = 640,
        classical_working_width: int = 320,
        canny_morph_max_boxes: int = 18,
        quad_rectangularity_max_boxes: int = 18,
        partial_contour_max_boxes: int = 18,
        photometric_windows_max_boxes: int = 18,
        hough_fragments_max_boxes: int = 12,
        corner_junctions_max_boxes: int = 12,
        adaptive_contours_max_boxes: int = 12,
        floor_working_width: int = 320,
        floor_horizon_ratio: float = 0.40,
        floor_edge_margin_ratio: float = 0.03,
        floor_seed_x0_ratio: float = 0.30,
        floor_seed_x1_ratio: float = 0.70,
        floor_seed_y0_ratio: float = 0.78,
        floor_seed_y1_ratio: float = 0.96,
        floor_color_distance_limit: float = 4.5,
        floor_texture_distance_limit: float = 4.0,
        floor_edge_quantile: float = 0.92,
        floor_minimum_edge_strength: float = 0.24,
        floor_minimum_floor_fraction: float = 0.08,
        floor_minimum_floor_support_px: int = 8,
        floor_minimum_interruption_run_px: int = 6,
        floor_minimum_boundary_width_ratio: float = 0.025,
        floor_minimum_boundary_confidence: float = 0.65,
        floor_max_boundaries: int = 8,
        raw_proposal_budget: int = 120,
        geometry_max_clusters: int = 12,
        geometry_cluster_iou_threshold: float = 0.12,
        geometry_cluster_center_distance: float = 0.07,
        geometry_split_spatial_modes: bool = True,
        geometry_split_center_gap: float = 0.14,
        geometry_max_hypotheses_per_cluster: int = 10,
        robust_extent_low_quantile: float = 0.10,
        robust_extent_high_quantile: float = 0.90,
        robust_extent_padding: float = 0.014,
        geometry_selector: str = "context_aware_heuristic",
        jev_enabled: bool = True,
        jev_model: str = "jev-latest",
        jev_api_url: str = "https://api.typesafe.ai/v1/systemone",
        jev_timeout_s: float = 30.0,
        cache_responses: bool = True,
        **config: Any,
    ) -> None:
        strategies = tuple(str(item) for item in (enabled_substrategies or self._DEFAULT_STRATEGIES))
        allowed = set(self._DEFAULT_STRATEGIES)
        unknown = sorted(set(strategies) - allowed)
        if unknown:
            raise ValueError(f"unsupported composite substrategy(s): {', '.join(unknown)}")
        if not strategies:
            raise ValueError("enabled_substrategies must contain at least one strategy")
        self.enabled_substrategies = strategies
        self.issue_working_width = max(320, int(issue_working_width))
        self.classical_working_width = max(160, int(classical_working_width))
        self.canny_morph_max_boxes = max(1, int(canny_morph_max_boxes))
        self.quad_rectangularity_max_boxes = max(1, int(quad_rectangularity_max_boxes))
        self.partial_contour_max_boxes = max(1, int(partial_contour_max_boxes))
        self.photometric_windows_max_boxes = max(1, int(photometric_windows_max_boxes))
        self.hough_fragments_max_boxes = max(1, int(hough_fragments_max_boxes))
        self.corner_junctions_max_boxes = max(1, int(corner_junctions_max_boxes))
        self.adaptive_contours_max_boxes = max(1, int(adaptive_contours_max_boxes))
        self.raw_proposal_budget = max(1, int(raw_proposal_budget))
        self.geometry_max_clusters = max(1, int(geometry_max_clusters))
        self.geometry_cluster_iou_threshold = max(
            0.0, min(1.0, float(geometry_cluster_iou_threshold))
        )
        self.geometry_cluster_center_distance = max(
            0.0, float(geometry_cluster_center_distance)
        )
        self.geometry_split_spatial_modes = bool(geometry_split_spatial_modes)
        self.geometry_split_center_gap = max(0.01, float(geometry_split_center_gap))
        self.geometry_max_hypotheses_per_cluster = max(
            6, int(geometry_max_hypotheses_per_cluster)
        )
        self.robust_extent_low_quantile = max(
            0.0, min(0.49, float(robust_extent_low_quantile))
        )
        self.robust_extent_high_quantile = max(
            self.robust_extent_low_quantile + 0.01,
            min(1.0, float(robust_extent_high_quantile)),
        )
        self.robust_extent_padding = max(0.0, min(0.25, float(robust_extent_padding)))
        self.geometry_selector = str(geometry_selector)
        if self.geometry_selector not in {
            "highest_raw_confidence",
            "covering_union",
            "robust_extent",
            "context_aware_heuristic",
        }:
            raise ValueError(
                "geometry_selector must be one of highest_raw_confidence, "
                "covering_union, robust_extent, context_aware_heuristic"
            )
        self.jev_enabled = bool(jev_enabled)
        self.jev_model = str(jev_model)
        self.jev_api_url = str(jev_api_url)
        self.jev_timeout_s = max(1.0, float(jev_timeout_s))
        self.cache_responses = bool(cache_responses)
        self.floor_config = FloorContinuityConfig(
            working_width=max(160, int(floor_working_width)),
            horizon_ratio=float(floor_horizon_ratio),
            edge_margin_ratio=float(floor_edge_margin_ratio),
            seed_x0_ratio=float(floor_seed_x0_ratio),
            seed_x1_ratio=float(floor_seed_x1_ratio),
            seed_y0_ratio=float(floor_seed_y0_ratio),
            seed_y1_ratio=float(floor_seed_y1_ratio),
            color_distance_limit=float(floor_color_distance_limit),
            texture_distance_limit=float(floor_texture_distance_limit),
            edge_quantile=float(floor_edge_quantile),
            minimum_edge_strength=float(floor_minimum_edge_strength),
            minimum_floor_fraction=float(floor_minimum_floor_fraction),
            minimum_floor_support_px=int(floor_minimum_floor_support_px),
            minimum_interruption_run_px=int(floor_minimum_interruption_run_px),
            minimum_boundary_width_ratio=float(floor_minimum_boundary_width_ratio),
            minimum_boundary_confidence=float(floor_minimum_boundary_confidence),
            max_boundaries=max(1, int(floor_max_boundaries)),
        )
        self._last_composite_summary: dict[str, Any] = {}
        self._current_frame_id = "unknown"
        super().__init__(**config)

    def perceive(self, inputs: PerceptionPluginInputs) -> PerceptionEvidenceBatch:
        self._current_frame_id = inputs.frame_id
        try:
            batch = super().perceive(inputs)
        finally:
            self._current_frame_id = "unknown"
        measurements = dict(batch.measurements)
        measurements["composite"] = self._last_composite_summary
        if inputs.diagnostics.enabled:
            inputs.diagnostics.emit_json(
                "composite_summary",
                "composite_summary.json",
                {
                    "frame_id": inputs.frame_id,
                    "strategies": list(self.enabled_substrategies),
                    **self._last_composite_summary,
                },
            )
        return PerceptionEvidenceBatch(
            signals=batch.signals,
            things=batch.things,
            measurements=measurements,
        )

    def _detect_candidates(
        self, rgb: np.ndarray
    ) -> tuple[list[PerceivedThing], np.ndarray, dict[str, Any]]:
        # Let the inherited current-edge detector normalize its raw input once.
        # The issue-219 edge/partial/line cues share one separately prepared
        # luminance image below; passing that image into the parent would apply
        # the configured normalization a second time to current edges.
        base_things, gray, base_summary = super()._detect_candidates(rgb)
        normalized_rgb = self._normalized_luminance_rgb(rgb)
        source_things: dict[str, list[PerceivedThing]] = {}
        source_things["current_edge_contours"] = [
            self._decorate("current_edge_contours", index, thing)
            for index, thing in enumerate(base_things)
        ]
        support_things: dict[str, list[PerceivedThing]] = {}
        floor_measurements: dict[str, Any] = {}

        if "edge_contours" in self.enabled_substrategies:
            source_things["canny_morph"] = self._issue_cues(
                "canny_morph",
                _canny_morph(
                    normalized_rgb,
                    working_width=self.issue_working_width,
                    max_boxes=self.canny_morph_max_boxes,
                    shared_blur_kernel=self.shared_blur_kernel,
                    shared_close_kernel=self.shared_close_kernel,
                    shared_min_width_px=self.shared_min_width_px,
                    shared_min_height_px=self.shared_min_height_px,
                ),
            )
        if "partial_faces" in self.enabled_substrategies:
            source_things["quad_rectangularity"] = self._issue_cues(
                "quad_rectangularity",
                _quad_rectangularity(
                    normalized_rgb,
                    working_width=self.issue_working_width,
                    max_boxes=self.quad_rectangularity_max_boxes,
                    shared_close_kernel=self.shared_close_kernel,
                    shared_min_width_px=self.shared_min_width_px,
                    shared_min_height_px=self.shared_min_height_px,
                    shared_max_vertices=self.shared_max_vertices,
                ),
            )
            source_things["partial_contour"] = self._issue_cues(
                "partial_contour",
                _partial_contour(
                    normalized_rgb,
                    working_width=self.issue_working_width,
                    max_boxes=self.partial_contour_max_boxes,
                    shared_blur_kernel=self.shared_blur_kernel,
                    shared_close_kernel=self.shared_close_kernel,
                    shared_min_width_px=self.shared_min_width_px,
                    shared_min_height_px=self.shared_min_height_px,
                ),
            )
        if "photometric_regions" in self.enabled_substrategies:
            source_things["classical_regions"], classical_summary = self._classical_regions(rgb)
            source_things["photometric_windows"] = self._issue_cues(
                "photometric_windows",
                _photometric_windows(
                    rgb,
                    working_width=self.issue_working_width,
                    max_boxes=self.photometric_windows_max_boxes,
                ),
            )
        else:
            classical_summary = {}
        if "floor_context" in self.enabled_substrategies:
            try:
                analysis = analyze_floor_continuity(rgb, self.floor_config)
                source_things["floor_continuity"] = [
                    self._floor_candidate(index, boundary, analysis)
                    for index, boundary in enumerate(analysis.boundaries)
                ]
                floor_measurements = dict(analysis.measurements)
            except Exception as exc:  # floor is one cue; preserve the other sources
                source_things["floor_continuity"] = []
                floor_measurements = {"error": f"{type(exc).__name__}: {exc}"}
        if "line_junction_support" in self.enabled_substrategies:
            support_things["corner_junctions"] = self._issue_cues(
                "corner_junctions",
                _corner_junctions(
                    normalized_rgb,
                    working_width=self.issue_working_width,
                    max_boxes=self.corner_junctions_max_boxes,
                ),
            )
            support_things["hough_fragments"] = self._issue_cues(
                "hough_fragments",
                _hough_fragments(
                    normalized_rgb,
                    working_width=self.issue_working_width,
                    max_boxes=self.hough_fragments_max_boxes,
                    shared_blur_kernel=self.shared_blur_kernel,
                ),
            )

        object_candidates = [thing for things in source_things.values() for thing in things]
        raw_proposal_records = [
            self._proposal_record(thing)
            for thing in object_candidates
        ]
        record_by_id = {
            record["thing_id"]: record for record in raw_proposal_records
        }
        object_candidates, suppression_rejections = self._suppress_correlated(object_candidates)
        for thing_id, reasons in suppression_rejections.items():
            record = record_by_id.get(thing_id)
            if record is not None:
                record["rejection_reasons"].extend(reasons)
        object_candidates = self._attach_line_support(
            object_candidates,
            [thing for things in support_things.values() for thing in things],
        )
        filtered = self._filter_candidates(tuple(object_candidates))
        filtered_ids = {thing.thing_id for thing in filtered}
        for thing in object_candidates:
            record = record_by_id[thing.thing_id]
            reasons = self._filter_rejection_reasons(thing)
            record["rejection_reasons"].extend(reasons)
            if thing.thing_id not in filtered_ids and not record["rejection_reasons"]:
                record["rejection_reasons"].append("candidate_budget_exceeded")
            record["pre_geometry_selected_for_tracking"] = thing.thing_id in filtered_ids
            record["selected_for_tracking"] = False
        support_records = [
            {
                **self._proposal_record(thing),
                "rejection_reasons": ["support_only_not_object_candidate"],
                "selected_for_tracking": False,
            }
            for things in support_things.values()
            for thing in things
        ]
        geometry_result = self._enumerate_geometry(
            object_candidates,
            filtered,
            raw_proposal_records,
        )
        tracking_candidates = geometry_result["tracking_candidates"]
        source_counts = {source: len(things) for source, things in source_things.items()}
        support_counts = {source: len(things) for source, things in support_things.items()}
        self._last_composite_summary = {
            "enabled_substrategies": list(self.enabled_substrategies),
            "raw_counts_by_source": source_counts,
            "support_counts_by_source": support_counts,
            "filtered_candidate_count": len(filtered),
            "tracking_candidate_count": len(tracking_candidates),
            "tracking_candidate_ids": [thing.thing_id for thing in tracking_candidates],
            "floor": floor_measurements,
            "classical_regions": classical_summary,
            "raw_source_proposals": raw_proposal_records,
            "support_proposals": support_records,
            "geometry": geometry_result["summary"],
            "candidate_sources": [
                {
                    "thing_id": thing.thing_id,
                    "source": thing.properties.get("source"),
                    "bbox_xyxy_norm": thing.location.bbox_xyxy_norm,
                    "confidence": thing.confidence,
                    "properties": dict(thing.properties),
                }
                for thing in tracking_candidates
            ],
            "line_junction_support": [
                {
                    "thing_id": thing.thing_id,
                    "source": thing.properties.get("source"),
                    "bbox_xyxy_norm": thing.location.bbox_xyxy_norm,
                    "confidence": thing.confidence,
                }
                for things in support_things.values()
                for thing in things
            ],
        }
        detector_summary = dict(base_summary)
        detector_summary.update(
            {
                "detector": "composite_shared_cv",
                "enabled_substrategies": list(self.enabled_substrategies),
                "raw_counts_by_source": source_counts,
                "support_counts_by_source": support_counts,
                "candidate_count_before_tracking": len(filtered),
                "candidate_count_after_geometry_selection": len(tracking_candidates),
                "raw_source_proposal_count": len(raw_proposal_records),
                "support_proposal_count": len(support_records),
                "raw_source_proposals": raw_proposal_records,
                "support_proposals": support_records,
                "geometry": geometry_result["summary"],
                "floor_measurements": floor_measurements,
                "shared_luminance_normalization": {
                    "mode": self.contrast_normalization,
                    "clip_limit": self.contrast_clip_limit,
                    "tile_size": self.contrast_tile_size,
                    "gamma": self.contrast_gamma,
                },
            }
        )
        detector_summary["candidate_count_before_tracking"] = len(tracking_candidates)
        return tracking_candidates, gray, detector_summary

    def _normalized_luminance_rgb(self, rgb: np.ndarray) -> np.ndarray:
        if self.contrast_normalization == "none":
            return np.array(rgb, copy=True)
        lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)
        lab[..., 0] = self._normalize_gray(rgb)
        return cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)

    def _issue_cues(self, source: str, things: list[PerceivedThing]) -> list[PerceivedThing]:
        return [self._decorate(source, index, thing) for index, thing in enumerate(things)]

    def _proposal_record(self, thing: PerceivedThing) -> dict[str, Any]:
        bbox = thing.location.bbox_xyxy_norm
        polygon = thing.location.polygon_xy_norm
        return {
            "thing_id": thing.thing_id,
            "source": thing.properties.get("source"),
            "strategy_family": thing.properties.get("strategy_family"),
            "confidence": thing.confidence,
            "geometry": {
                "frame": thing.location.frame,
                "zone": thing.location.zone,
                "bbox_xyxy_norm": list(bbox) if bbox is not None else None,
                "polygon_xy_norm": (
                    [list(point) for point in polygon] if polygon is not None else None
                ),
            },
            "properties": _json_safe(thing.properties),
            "rejection_reasons": [],
            "selected_for_tracking": False,
        }

    def _decorate(self, source: str, index: int, thing: PerceivedThing) -> PerceivedThing:
        properties = dict(thing.properties)
        family = "edge_family" if source in {"current_edge_contours", "canny_morph", "quad_rectangularity", "partial_contour"} else self._STRATEGY_FAMILIES.get(source, source)
        properties.update(
            {
                "source": source,
                "strategy_family": family,
                "polygon_xy_norm": thing.location.polygon_xy_norm,
                "source_thing_id": thing.thing_id,
            }
        )
        return PerceivedThing(
            thing_id=f"composite_{source}_{index:03d}",
            kind="region_proposal",
            label=thing.label,
            location=thing.location,
            confidence=thing.confidence,
            properties=properties,
            source_plugin_id=self.plugin_id,
        )

    def _classical_regions(self, rgb: np.ndarray) -> tuple[list[PerceivedThing], dict[str, Any]]:
        proposals, diagnostic = _detect_regions(
            rgb,
            working_width=self.classical_working_width,
            spatial_radius=8,
            color_radius=18,
            min_area_fraction=max(0.0005, self.minimum_object_area_fraction / 3.0),
            max_area_fraction=self.maximum_object_area_fraction,
            max_regions=24,
        )
        things: list[PerceivedThing] = []
        for index, proposal in enumerate(proposals):
            bbox = tuple(float(value) for value in proposal["bbox"])
            things.append(
                self._decorate(
                    "classical_regions",
                    index,
                    PerceivedThing(
                        thing_id=f"classical_region_{index:03d}",
                        kind="region_proposal",
                        label="coherent color region",
                        location=ViewLocation(
                            frame="image",
                            zone=_zone(bbox),
                            bbox_xyxy_norm=bbox,
                            polygon_xy_norm=tuple(
                                tuple(float(value) for value in point)
                                for point in proposal.get("contour", [])
                            ),
                        ),
                        confidence=float(proposal["confidence"]),
                        properties={
                            "evidence": "classical_color_component",
                            "area_fraction": proposal["area_fraction"],
                            "centroid_xy_norm": proposal["centroid"],
                            "contour_xy_norm": proposal["contour"],
                            "touches_lower_image": proposal["touches_lower_image"],
                            "color_coherence": proposal["color_coherence"],
                            "solidity": proposal["solidity"],
                            "raw_rgb_features": True,
                        },
                    ),
                )
            )
        return things, {
            "working_width": diagnostic.get("working_width"),
            "working_height": diagnostic.get("working_height"),
            "component_count": diagnostic.get("component_count"),
        }

    def _floor_candidate(self, index: int, boundary: dict[str, Any], analysis: Any) -> PerceivedThing:
        bbox = tuple(float(value) for value in boundary["bbox_xyxy_norm"])
        polygon = tuple(
            tuple(float(value) for value in point)
            for point in boundary.get("polygon_xy_norm", [])
        )
        properties = {
            "evidence": "multi_cue_floor_continuity_interruption",
            "source": "floor_continuity",
            "strategy_family": "floor_family",
            "floor_support_below": boundary.get("floor_support_below"),
            "width_fraction": boundary.get("width_fraction"),
            "edge_agreement": boundary.get("edge_agreement"),
            "vertical_consistency": boundary.get("vertical_consistency"),
            "color_discontinuity": boundary.get("color_discontinuity"),
            "texture_discontinuity": boundary.get("texture_discontinuity"),
            "cue_agreement": boundary.get("cue_agreement"),
            "ambiguity": boundary.get("ambiguity"),
            "edge_density": boundary.get("edge_agreement", 0.0),
            "rectangularity": boundary.get("vertical_consistency", 0.0),
            "raw_rgb_features": True,
        }
        return PerceivedThing(
            thing_id=f"composite_floor_continuity_{index:03d}",
            kind="region_proposal",
            label="supported floor interruption",
            location=ViewLocation(
                frame="image",
                zone=_zone(bbox),
                bbox_xyxy_norm=bbox,
                polygon_xy_norm=polygon,
            ),
            confidence=float(boundary["confidence"]),
            properties=properties,
            source_plugin_id=self.plugin_id,
        )

    def _suppress_correlated(
        self, candidates: list[PerceivedThing]
    ) -> tuple[list[PerceivedThing], dict[str, list[str]]]:
        kept: list[PerceivedThing] = []
        rejections: dict[str, list[str]] = {}
        ordered = sorted(candidates, key=lambda item: item.confidence, reverse=True)
        for candidate in ordered:
            bbox = candidate.location.bbox_xyxy_norm
            if bbox is None:
                rejections[candidate.thing_id] = ["missing_geometry"]
                continue
            family = candidate.properties.get("strategy_family")
            duplicate = False
            for existing in kept:
                existing_bbox = existing.location.bbox_xyxy_norm
                if existing_bbox is None or existing.properties.get("strategy_family") != family:
                    continue
                threshold = 0.76 if family == "edge_family" else 0.64
                if _bbox_iou(bbox, existing_bbox) >= threshold:
                    duplicate = True
                    break
            if not duplicate:
                kept.append(candidate)
            else:
                rejections[candidate.thing_id] = [
                    "correlated_duplicate",
                    f"correlated_family:{family}",
                ]
        return kept, rejections

    def _filter_rejection_reasons(self, thing: PerceivedThing) -> list[str]:
        """Mirror inherited filtering so each raw proposal has an explanation."""
        reasons: list[str] = []
        if thing.kind != "region_proposal":
            return ["unsupported_candidate_kind"]
        bbox = thing.location.bbox_xyxy_norm
        if bbox is None or len(bbox) != 4:
            return ["missing_geometry"]
        x1, y1, x2, y2 = (float(value) for value in bbox)
        width = max(0.0, x2 - x1)
        height = max(0.0, y2 - y1)
        area = width * height
        if height < self.minimum_object_height:
            reasons.append("minimum_object_height")
        if area < self.minimum_object_area_fraction:
            reasons.append("minimum_object_area_fraction")
        if area > self.maximum_object_area_fraction:
            reasons.append("maximum_object_area_fraction")
        if (
            y1 >= self.floor_cutoff_y - 0.12
            and bool(thing.properties.get("touches_lower_image"))
        ):
            reasons.append("floor_like_lower_region")
        if y1 >= self.floor_cutoff_y and height < 0.25:
            reasons.append("lower_short_region")
        if width > 0.80 and height < 0.35:
            reasons.append("full_width_band")
        if x1 >= 0.88 and width < 0.15:
            reasons.append("right_edge_strip")
        if y1 < 0.05 and x1 < 0.65 and width < 0.12:
            reasons.append("top_edge_detail")
        return reasons

    def _enumerate_geometry(
        self,
        candidates: list[PerceivedThing],
        fallback_candidates: list[PerceivedThing],
        raw_records: list[dict[str, Any]],
    ) -> dict[str, Any]:
        proposals: list[GeometryProposal] = []
        record_by_id = {record["thing_id"]: record for record in raw_records}
        for record in raw_records:
            proposal = proposal_from_record(record)
            if proposal is None:
                record["rejection_reasons"].append("geometry_missing_or_invalid")
                continue
            proposals.append(proposal)

        budgeted, budget_rejections = source_balanced_budget(
            proposals,
            limit=self.raw_proposal_budget,
        )
        for proposal_id, reasons in budget_rejections.items():
            if proposal_id in record_by_id:
                record_by_id[proposal_id]["rejection_reasons"].extend(reasons)

        base_clusters, split_clusters, cluster_rejections, split_events = cluster_proposals(
            budgeted,
            iou_threshold=self.geometry_cluster_iou_threshold,
            center_distance_threshold=self.geometry_cluster_center_distance,
            max_clusters=self.geometry_max_clusters,
            split_spatial_modes=self.geometry_split_spatial_modes,
            split_center_gap=self.geometry_split_center_gap,
        )
        for proposal_id, reasons in cluster_rejections.items():
            if proposal_id in record_by_id:
                record_by_id[proposal_id]["rejection_reasons"].extend(reasons)

        active_variant = "split" if self.geometry_split_spatial_modes else "base"
        active_clusters = split_clusters if active_variant == "split" else base_clusters
        if not active_clusters:
            active_clusters = base_clusters
            active_variant = "base"
        base_hypotheses = {
            cluster_id: make_hypotheses(
                cluster_id,
                members,
                max_hypotheses=self.geometry_max_hypotheses_per_cluster,
                robust_low_quantile=self.robust_extent_low_quantile,
                robust_high_quantile=self.robust_extent_high_quantile,
                robust_padding=self.robust_extent_padding,
            )
            for cluster_id, members in base_clusters.items()
        }
        active_hypotheses = {
            cluster_id: make_hypotheses(
                cluster_id,
                members,
                max_hypotheses=self.geometry_max_hypotheses_per_cluster,
                robust_low_quantile=self.robust_extent_low_quantile,
                robust_high_quantile=self.robust_extent_high_quantile,
                robust_padding=self.robust_extent_padding,
            )
            for cluster_id, members in active_clusters.items()
        }
        selectors = compare_selectors(active_hypotheses)
        chosen_by_selector = selectors.get(self.geometry_selector, {})
        selected_hypotheses = [
            hypothesis
            for hypothesis in chosen_by_selector.values()
            if hypothesis.valid
        ]
        selected_things = [
            self._hypothesis_thing(hypothesis, self.geometry_selector)
            for hypothesis in selected_hypotheses
        ]
        selected_things = [thing for thing in selected_things if thing is not None]
        selected_things = self._filter_candidates(tuple(selected_things))
        tracking_candidates = selected_things
        actual_source_ids: set[str] = set()
        geometry_source_ids: set[str] = set()
        for thing in tracking_candidates:
            if thing.thing_id.startswith("composite_geometry_"):
                geometry_source_ids.update(thing.properties.get("source_ids") or ())
                actual_source_ids.update(thing.properties.get("source_ids") or ())
            else:
                actual_source_ids.add(thing.thing_id)
        for record in raw_records:
            record["source_supports_tracking"] = record["thing_id"] in actual_source_ids
            record["selected_for_tracking"] = False
            record["geometry_selected_for_tracking"] = record["thing_id"] in geometry_source_ids

        jev_request = build_jev_request(
            self._current_frame_id,
            budgeted,
            active_clusters,
            active_hypotheses,
            selectors,
            model=self.jev_model,
        )
        jev = self._run_jev_comparison(jev_request, active_hypotheses)
        summary = {
            "raw_proposal_budget": self.raw_proposal_budget,
            "raw_proposal_count": len(proposals),
            "budgeted_proposal_count": len(budgeted),
            "discarded_proposals": [
                {
                    "proposal": proposal.to_dict(),
                    "rejection_reasons": list(
                        record_by_id.get(proposal.proposal_id, {}).get(
                            "rejection_reasons", []
                        )
                    ),
                }
                for proposal in proposals
                if proposal.proposal_id in budget_rejections
                or proposal.proposal_id in cluster_rejections
            ],
            "budgeted_proposals": [proposal.to_dict() for proposal in budgeted],
            "base_clusters": {
                cluster_id: [proposal.to_dict() for proposal in members]
                for cluster_id, members in base_clusters.items()
            },
            "split_clusters": {
                cluster_id: [proposal.to_dict() for proposal in members]
                for cluster_id, members in split_clusters.items()
            },
            "active_cluster_variant": active_variant,
            "split_events": split_events,
            "base_hypotheses": {
                cluster_id: [hypothesis.to_dict() for hypothesis in hypotheses]
                for cluster_id, hypotheses in base_hypotheses.items()
            },
            "hypotheses": {
                cluster_id: [hypothesis.to_dict() for hypothesis in hypotheses]
                for cluster_id, hypotheses in active_hypotheses.items()
            },
            "selector_choices": {
                selector: {
                    cluster_id: hypothesis.to_dict()
                    for cluster_id, hypothesis in choices.items()
                }
                for selector, choices in selectors.items()
            },
            "active_selector": self.geometry_selector,
            "selected_hypotheses": [hypothesis.to_dict() for hypothesis in selected_hypotheses],
            "selected_tracking_boxes": [
                {
                    "thing_id": thing.thing_id,
                    "bbox_xyxy_norm": thing.location.bbox_xyxy_norm,
                    "properties": dict(thing.properties),
                }
                for thing in tracking_candidates
                if thing.thing_id.startswith("composite_geometry_")
            ],
            "tracking_candidates": [
                {
                    "thing_id": thing.thing_id,
                    "bbox_xyxy_norm": thing.location.bbox_xyxy_norm,
                    "confidence": thing.confidence,
                    "properties": dict(thing.properties),
                }
                for thing in tracking_candidates
            ],
            "tracking_candidate_ids": [thing.thing_id for thing in tracking_candidates],
            "jev": jev,
            "fixed_jev_prompt": FIXED_JEV_PROMPT,
        }
        return {"tracking_candidates": tracking_candidates, "summary": summary}

    def _hypothesis_thing(
        self,
        hypothesis: GeometryHypothesis,
        selector: str,
    ) -> PerceivedThing | None:
        if not hypothesis.valid or hypothesis.kind == "none":
            return None
        return PerceivedThing(
            thing_id=(
                f"composite_geometry_{hypothesis.cluster_id}_{hypothesis.kind}"
            ),
            kind="region_proposal",
            label=f"geometry hypothesis: {hypothesis.kind}",
            location=ViewLocation(
                frame="image",
                zone=_zone(hypothesis.bbox),
                bbox_xyxy_norm=hypothesis.bbox,
            ),
            confidence=hypothesis.confidence,
            properties={
                "evidence": "deterministic_geometry_selector",
                "source": "geometry_selector",
                "strategy_family": "geometry_family",
                "geometry_selector": selector,
                "cluster_id": hypothesis.cluster_id,
                "hypothesis_id": hypothesis.hypothesis_id,
                "hypothesis_kind": hypothesis.kind,
                "source_ids": list(hypothesis.source_ids),
                "source_names": list(hypothesis.source_names),
                "edge_density": hypothesis.confidence,
                "rectangularity": hypothesis.confidence,
                "area_fraction": (
                    max(0.0, hypothesis.bbox[2] - hypothesis.bbox[0])
                    * max(0.0, hypothesis.bbox[3] - hypothesis.bbox[1])
                ),
            },
            source_plugin_id=self.plugin_id,
        )

    def _run_jev_comparison(
        self,
        request: dict[str, Any],
        hypotheses_by_cluster: dict[str, list[GeometryHypothesis]],
    ) -> dict[str, Any]:
        request_digest = hashlib.sha256(
            _canonical_json(request).encode("utf-8")
        ).hexdigest()
        cache_path = (
            Path(__file__).resolve().parents[1]
            / "cache"
            / f"geometry-v1-{request_digest}.json"
        )
        if cache_path.is_file():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                response = cached.get("response")
                return {
                    "status": "cached",
                    "model": self.jev_model,
                    "request_sha256": request_digest,
                    "cache_path": str(cache_path),
                    "selected": {
                        cluster_id: hypothesis.to_dict()
                        for cluster_id, hypothesis in select_jev_hypotheses(
                            response, hypotheses_by_cluster
                        ).items()
                    },
                    "response": _json_safe(response),
                }
            except (OSError, TypeError, ValueError):
                pass
        if not self.jev_enabled:
            return {
                "status": "disabled",
                "model": self.jev_model,
                "request_sha256": request_digest,
                "cache_path": str(cache_path),
                "selected": {},
            }
        api_key = os.environ.get("JEV_API_KEY") or os.environ.get("TYPESAFE_API_KEY")
        if not api_key:
            return {
                "status": "not evaluated: missing_api_key",
                "model": self.jev_model,
                "request_sha256": request_digest,
                "cache_path": str(cache_path),
                "selected": {},
                "evaluated": False,
                "reason": "missing_api_key",
            }
        try:
            import requests

            response = requests.post(
                self.jev_api_url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=request,
                timeout=self.jev_timeout_s,
            )
            response.raise_for_status()
            payload = response.json()
            if self.cache_responses:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(
                    json.dumps(
                        {
                            "schema": "composite_geometry_jev_cache_v1",
                            "request": request,
                            "response": payload,
                        },
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n",
                    encoding="utf-8",
                )
            return {
                "status": "ok",
                "model": self.jev_model,
                "request_sha256": request_digest,
                "cache_path": str(cache_path),
                "selected": {
                    cluster_id: hypothesis.to_dict()
                    for cluster_id, hypothesis in select_jev_hypotheses(
                        payload, hypotheses_by_cluster
                    ).items()
                },
                "response": _json_safe(payload),
            }
        except Exception as exc:  # Jev is comparative evidence, never runtime truth.
            return {
                "status": "error",
                "model": self.jev_model,
                "request_sha256": request_digest,
                "cache_path": str(cache_path),
                "selected": {},
                "error": f"{type(exc).__name__}: {exc}",
            }

    def _attach_line_support(
        self,
        candidates: list[PerceivedThing],
        supports: list[PerceivedThing],
    ) -> list[PerceivedThing]:
        if not supports:
            return candidates
        updated: list[PerceivedThing] = []
        for candidate in candidates:
            bbox = candidate.location.bbox_xyxy_norm
            if bbox is None:
                updated.append(candidate)
                continue
            nearby = []
            for support in supports:
                support_bbox = support.location.bbox_xyxy_norm
                if support_bbox is None:
                    continue
                if _bbox_iou(bbox, support_bbox) >= 0.03 or _center_distance(bbox, support_bbox) <= 0.10:
                    nearby.append(support)
            properties = dict(candidate.properties)
            properties["line_junction_support_count"] = len(nearby)
            properties["line_junction_support_sources"] = sorted(
                {str(item.properties.get("source")) for item in nearby}
            )
            properties["line_junction_support_confidence"] = round(
                max((item.confidence for item in nearby), default=0.0), 5
            )
            updated.append(
                PerceivedThing(
                    thing_id=candidate.thing_id,
                    kind=candidate.kind,
                    label=candidate.label,
                    location=candidate.location,
                    confidence=candidate.confidence,
                    properties=properties,
                    source_plugin_id=candidate.source_plugin_id,
                )
            )
        return updated


def _center_distance(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> float:
    return float(
        np.hypot(
            ((left[0] + left[2]) / 2.0) - ((right[0] + right[2]) / 2.0),
            ((left[1] + left[3]) / 2.0) - ((right[1] + right[3]) / 2.0),
        )
    )


def _json_safe(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _json_safe(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
