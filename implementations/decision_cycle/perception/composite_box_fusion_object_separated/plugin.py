"""Composite fusion variant with recursive separation of spatial modes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from implementations.decision_cycle.perception.composite_box_fusion.plugin import *  # noqa: F401,F403
from implementations.decision_cycle.perception.composite_box_fusion.plugin import (
    CompositeBoxFusionPlugin as _CompositeBoxFusionPlugin,
    _canonical_json,
    _center_distance,
    _json_safe,
)


class CompositeBoxFusionPlugin(_CompositeBoxFusionPlugin):
    """Keep the variant entrypoint and its object separation default."""

    plugin_id = "composite_box_fusion_object_separated"
    _emit_object_separated_geometry = True
    _jev_cache_directory = Path(__file__).resolve().parent / "cache"

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
        object_separated_geometry: bool = True,
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
        super().__init__(
            enabled_substrategies=enabled_substrategies,
            issue_working_width=issue_working_width,
            classical_working_width=classical_working_width,
            canny_morph_max_boxes=canny_morph_max_boxes,
            quad_rectangularity_max_boxes=quad_rectangularity_max_boxes,
            partial_contour_max_boxes=partial_contour_max_boxes,
            photometric_windows_max_boxes=photometric_windows_max_boxes,
            hough_fragments_max_boxes=hough_fragments_max_boxes,
            corner_junctions_max_boxes=corner_junctions_max_boxes,
            adaptive_contours_max_boxes=adaptive_contours_max_boxes,
            floor_working_width=floor_working_width,
            floor_horizon_ratio=floor_horizon_ratio,
            floor_edge_margin_ratio=floor_edge_margin_ratio,
            floor_seed_x0_ratio=floor_seed_x0_ratio,
            floor_seed_x1_ratio=floor_seed_x1_ratio,
            floor_seed_y0_ratio=floor_seed_y0_ratio,
            floor_seed_y1_ratio=floor_seed_y1_ratio,
            floor_color_distance_limit=floor_color_distance_limit,
            floor_texture_distance_limit=floor_texture_distance_limit,
            floor_edge_quantile=floor_edge_quantile,
            floor_minimum_edge_strength=floor_minimum_edge_strength,
            floor_minimum_floor_fraction=floor_minimum_floor_fraction,
            floor_minimum_floor_support_px=floor_minimum_floor_support_px,
            floor_minimum_interruption_run_px=floor_minimum_interruption_run_px,
            floor_minimum_boundary_width_ratio=floor_minimum_boundary_width_ratio,
            floor_minimum_boundary_confidence=floor_minimum_boundary_confidence,
            floor_max_boundaries=floor_max_boundaries,
            raw_proposal_budget=raw_proposal_budget,
            geometry_max_clusters=geometry_max_clusters,
            geometry_cluster_iou_threshold=geometry_cluster_iou_threshold,
            geometry_cluster_center_distance=geometry_cluster_center_distance,
            geometry_split_spatial_modes=geometry_split_spatial_modes,
            geometry_split_center_gap=geometry_split_center_gap,
            object_separated_geometry=object_separated_geometry,
            geometry_max_hypotheses_per_cluster=geometry_max_hypotheses_per_cluster,
            robust_extent_low_quantile=robust_extent_low_quantile,
            robust_extent_high_quantile=robust_extent_high_quantile,
            robust_extent_padding=robust_extent_padding,
            geometry_selector=geometry_selector,
            jev_enabled=jev_enabled,
            jev_model=jev_model,
            jev_api_url=jev_api_url,
            jev_timeout_s=jev_timeout_s,
            cache_responses=cache_responses,
            **config,
        )
