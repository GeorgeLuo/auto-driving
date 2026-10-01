"""Packaged perception plugins and named perception selections.

``PERCEPTION_ALGORITHMS`` names ready-made perception selections: the plugins
to select, in order, and config overrides for some of them. An activation
built from one records the algorithm name in its metadata.
"""

from __future__ import annotations

from typing import Any

from autonomy.decision_cycle.perception.interface import PERCEPTION_TEXT_SCHEMA


DEFAULT_PERCEPTION_ALGORITHM = "lightweight_observer"

# Tuned default configs shared by related packaged plugins.
_FLOOR_CONTINUITY_CONFIG: dict[str, Any] = {
    "working_width": 320,
    "horizon_ratio": 0.4,
    "edge_margin_ratio": 0.03,
    "seed_x0_ratio": 0.3,
    "seed_x1_ratio": 0.7,
    "seed_y0_ratio": 0.78,
    "seed_y1_ratio": 0.96,
    "color_distance_limit": 4.5,
    "texture_distance_limit": 4.0,
    "edge_quantile": 0.92,
    "minimum_edge_strength": 0.24,
    "minimum_floor_fraction": 0.08,
    "minimum_floor_support_px": 8,
    "minimum_interruption_run_px": 6,
    "minimum_boundary_width_ratio": 0.025,
    "minimum_boundary_confidence": 0.65,
    "max_boundaries": 8
}

_COMPOSITE_BOX_FUSION_CONFIG: dict[str, Any] = {
    "enabled_substrategies": [
        "edge_contours",
        "partial_faces",
        "photometric_regions",
        "floor_context",
        "line_junction_support"
    ],
    "max_tracks": 4,
    "floor_cutoff_y": 0.72,
    "minimum_object_height": 0.1,
    "minimum_object_area_fraction": 0.002,
    "maximum_object_area_fraction": 0.6,
    "association_distance": 0.35,
    "minimum_association_score": 0.12,
    "smoothing_alpha": 0.65,
    "max_missed_frames": 1,
    "reacquire_window_frames": 4,
    "minimum_feature_points": 6,
    "canny_low": 30,
    "canny_high": 45,
    "minimum_contour_area_fraction": 0.0005,
    "maximum_contour_area_fraction": 0.6,
    "contour_merge_gap": 0.08,
    "contrast_normalization": "none",
    "contrast_clip_limit": 2.0,
    "contrast_tile_size": 8,
    "contrast_gamma": 1.0,
    "shared_blur_kernel": 0,
    "shared_close_kernel": 0,
    "shared_min_width_px": 0,
    "shared_min_height_px": 0,
    "shared_max_vertices": 0,
    "preserve_separate_proposals": False,
    "duplicate_suppression_iou": 0.08,
    "output_bbox_shrink_x": 1.0,
    "output_bbox_shrink_y": 1.0,
    "minimum_output_confidence": 0.0,
    "issue_working_width": 640,
    "classical_working_width": 320,
    "canny_morph_max_boxes": 18,
    "quad_rectangularity_max_boxes": 18,
    "partial_contour_max_boxes": 18,
    "photometric_windows_max_boxes": 18,
    "hough_fragments_max_boxes": 12,
    "corner_junctions_max_boxes": 12,
    "adaptive_contours_max_boxes": 12,
    "floor_working_width": 320,
    "floor_horizon_ratio": 0.4,
    "floor_edge_margin_ratio": 0.03,
    "floor_seed_x0_ratio": 0.3,
    "floor_seed_x1_ratio": 0.7,
    "floor_seed_y0_ratio": 0.78,
    "floor_seed_y1_ratio": 0.96,
    "floor_color_distance_limit": 4.5,
    "floor_texture_distance_limit": 4.0,
    "floor_edge_quantile": 0.92,
    "floor_minimum_edge_strength": 0.24,
    "floor_minimum_floor_fraction": 0.08,
    "floor_minimum_floor_support_px": 8,
    "floor_minimum_interruption_run_px": 6,
    "floor_minimum_boundary_width_ratio": 0.025,
    "floor_minimum_boundary_confidence": 0.65,
    "floor_max_boundaries": 8,
    "raw_proposal_budget": 120,
    "geometry_max_clusters": 12,
    "geometry_cluster_iou_threshold": 0.12,
    "geometry_cluster_center_distance": 0.07,
    "geometry_split_spatial_modes": True,
    "geometry_split_center_gap": 0.14,
    "geometry_max_hypotheses_per_cluster": 10,
    "robust_extent_low_quantile": 0.1,
    "robust_extent_high_quantile": 0.9,
    "robust_extent_padding": 0.014,
    "geometry_selector": "context_aware_heuristic",
    "manual_annotations_runtime_input": False,
    "jev_enabled": True,
    "jev_model": "jev-latest",
    "jev_api_url": "https://api.typesafe.ai/v1/systemone",
    "jev_timeout_s": 30.0,
    "cache_responses": True
}

_MULTI_OBSTRUCTION_TRACKS_CONFIG: dict[str, Any] = {
    "max_tracks": 2,
    "floor_cutoff_y": 0.5,
    "minimum_object_height": 0.14,
    "minimum_object_area_fraction": 0.006,
    "maximum_object_area_fraction": 0.3,
    "association_distance": 0.35,
    "minimum_association_score": 0.12,
    "smoothing_alpha": 0.35,
    "max_missed_frames": 2,
    "reacquire_window_frames": 4,
    "minimum_feature_points": 6,
    "canny_low": 30,
    "canny_high": 45,
    "minimum_contour_area_fraction": 0.003,
    "maximum_contour_area_fraction": 0.25,
    "contour_merge_gap": 0.08,
    "contrast_normalization": "none",
    "contrast_clip_limit": 2.0,
    "contrast_tile_size": 8,
    "contrast_gamma": 1.0,
    "preserve_separate_proposals": False,
    "duplicate_suppression_iou": 0.08,
    "output_bbox_shrink_x": 1.0,
    "output_bbox_shrink_y": 1.0,
    "minimum_output_confidence": 0.0
}

# Each plugin declares its own ID (its ``plugin_id``); entries do not repeat it.
PERCEPTION_PLUGINS: tuple[dict[str, Any], ...] = (
    {
        "spec": "implementations.decision_cycle.perception.plugins.floor_plane.plugin:FloorPlanePlugin",
        "description": "",
        "default_config": {},
    },
    {
        "spec": "implementations.decision_cycle.perception.plugins.frame.plugin:FrameObservationPlugin",
        "description": "",
        "default_config": {},
    },
    {
        "spec": "implementations.decision_cycle.perception.plugins.motion_tracks.plugin:MotionTracksPlugin",
        "description": "",
        "default_config": {},
    },
    {
        "spec": "implementations.decision_cycle.perception.plugins.obstruction_tracks.plugin:MultiObstructionTracksPlugin",
        "description": "",
        "default_config": {},
    },
    {
        "spec": "implementations.decision_cycle.perception.plugins.sim_color_targets.plugin:SimColorTargetsPlugin",
        "description": "",
        "default_config": {},
    },
    {
        "spec": "implementations.decision_cycle.perception.plugins.vlm_prep.plugin:VlmPrepPlugin",
        "description": "",
        "default_config": {},
    },
    {
        "spec": "implementations.decision_cycle.perception.plugins.classical_regions.plugin:ClassicalRegionPlugin",
        "description": "OpenCV-only coherent color components as generic image-space regions.",
        "default_config": {"working_width": 320, "spatial_radius": 8, "color_radius": 18, "min_area_fraction": 0.003, "max_area_fraction": 0.65, "max_regions": 32},
    },
    {
        "spec": "implementations.decision_cycle.perception.plugins.composite_box_fusion.plugin:CompositeBoxFusionPlugin",
        "description": "Composite obstruction candidates from edge contours, partial-face and photometric cues, floor continuity, and line/junction support.",
        "default_config": _COMPOSITE_BOX_FUSION_CONFIG,
    },
    {
        "spec": "implementations.decision_cycle.perception.plugins.composite_box_fusion_object_separated.plugin:CompositeBoxFusionPlugin",
        "description": "Composite box fusion with recursive spatial-mode separation between objects.",
        "default_config": {**_COMPOSITE_BOX_FUSION_CONFIG, "object_separated_geometry": True},
    },
    {
        "spec": "implementations.decision_cycle.perception.plugins.floor_continuity.plugin:FloorContinuityPlugin",
        "description": "Stateless multi-cue bottom-connected floor support and interruption evidence.",
        "default_config": _FLOOR_CONTINUITY_CONFIG,
    },
    {
        "spec": "implementations.decision_cycle.perception.plugins.floor_continuity_capture.plugin:CaptureFloorContinuityPlugin",
        "description": "Stricter floor-boundary variant calibrated against the archived Chaser depth-obstacle capture.",
        "default_config": {"minimum_boundary_width_ratio": 0.03, "minimum_boundary_confidence": 0.7},
    },
    {
        "spec": "implementations.decision_cycle.perception.plugins.floor_continuity_temporal.plugin:TemporalFloorContinuityPlugin",
        "description": "Temporal association and box smoothing around the floor-continuity cue.",
        "default_config": {**_FLOOR_CONTINUITY_CONFIG, "smoothing_alpha": 0.45, "association_distance": 0.3, "minimum_association_score": 0.18, "max_hold_frames": 2},
    },
    {
        "spec": "implementations.decision_cycle.perception.plugins.multi_obstruction_tracks.plugin:MultiObstructionTracksPlugin",
        "description": "Floor-suppressed obstruction candidates for the multi_obstruction_tracks memory plugin to track.",
        "default_config": _MULTI_OBSTRUCTION_TRACKS_CONFIG,
    },
)

PERCEPTION_ALGORITHMS: dict[str, dict[str, Any]] = {
    "lightweight_observer": {
        "description": (
            "Lightweight perception: frame facts, visible floor, and "
            "first-hit floor boundaries."
        ),
        "plugins": ["frame", "floor_plane"],
        "output_contract": {
            "schema": PERCEPTION_TEXT_SCHEMA,
            "meaning": "structured frame, floor, and non-semantic boundary evidence",
        },
    },
    "sim_debug": {
        "description": (
            "Simulator-only debug control: frame facts plus known Chase "
            "color-target signals."
        ),
        "plugins": ["frame", "sim_color_targets"],
        "output_contract": {
            "schema": PERCEPTION_TEXT_SCHEMA,
            "meaning": "structured frame and simulator target evidence",
        },
    },
    "visual_observer": {
        "description": (
            "Generic visual observer: frame facts, floor/traversability, and "
            "bounded scene tracks."
        ),
        "plugins": ["frame", "floor_plane", "motion_tracks"],
        "output_contract": {
            "schema": PERCEPTION_TEXT_SCHEMA,
            "meaning": "structured surface, boundary, and scene-track evidence",
        },
    },
    "obstruction_observer": {
        "description": (
            "Generic obstruction observer: frame facts, floor suppression, and "
            "bounded multi-region temporal tracks."
        ),
        "plugins": ["frame", "floor_plane", "obstruction_tracks"],
        "plugin_configs": {
            "obstruction_tracks": {
                "max_tracks": 4,
                "floor_cutoff_y": 0.72,
                "minimum_object_height": 0.10,
                "minimum_object_area_fraction": 0.006,
                "maximum_object_area_fraction": 0.60,
                "association_distance": 0.35,
                "minimum_association_score": 0.12,
                "smoothing_alpha": 0.35,
                "max_missed_frames": 2,
                "reacquire_window_frames": 4,
                "minimum_feature_points": 6,
                "canny_low": 20,
                "canny_high": 40,
                "minimum_contour_area_fraction": 0.0015,
                "maximum_contour_area_fraction": 0.25,
                "contour_merge_gap": 0.16,
            }
        },
        "output_contract": {
            "schema": PERCEPTION_TEXT_SCHEMA,
            "meaning": "structured frame, floor, and generic obstruction-track evidence",
        },
    },
}


def available_perception_algorithm_ids() -> tuple[str, ...]:
    return tuple(sorted(PERCEPTION_ALGORITHMS))


DEFAULT_PERCEPTION_PLUGINS: tuple[str, ...] = tuple(
    PERCEPTION_ALGORITHMS[DEFAULT_PERCEPTION_ALGORITHM]["plugins"]
)
