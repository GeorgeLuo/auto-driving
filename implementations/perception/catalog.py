from __future__ import annotations

from typing import Any

from autonomy.perception import PERCEPTION_TEXT_SCHEMA


PERCEPTION_MAPPER_SPEC = (
    "autonomy.perception.mappers.plugin_runner:PluginPerceptionMapper"
)
DEFAULT_PERCEPTION_ALGORITHM = "lightweight_observer"

PERCEPTION_PLUGIN_SPECS: dict[str, str] = {
    "floor_plane": "implementations.perception.traversability.plugin:FloorPlanePlugin",
    "frame": "implementations.perception.observation.plugin:FrameObservationPlugin",
    "motion_tracks": "implementations.perception.motion.tracks:MotionTracksPlugin",
    "obstruction_tracks": (
        "implementations.perception.obstruction_tracks:MultiObstructionTracksPlugin"
    ),
    "sim_color_targets": (
        "implementations.perception.simulation.color_targets:SimColorTargetsPlugin"
    ),
    "vlm_prep": "implementations.perception.preparation.vlm:VlmPrepPlugin",
}

PERCEPTION_ALGORITHMS: dict[str, dict[str, Any]] = {
    "lightweight_observer": {
        "description": (
            "Lightweight generic observer: frame facts, visible floor, and "
            "first-hit floor boundaries."
        ),
        "mapper_spec": PERCEPTION_MAPPER_SPEC,
        "mapper_config": {
            "plugins": ["frame", "floor_plane"],
            "plugin_specs": dict(PERCEPTION_PLUGIN_SPECS),
        },
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
        "mapper_spec": PERCEPTION_MAPPER_SPEC,
        "mapper_config": {
            "plugins": ["frame", "sim_color_targets"],
            "plugin_specs": dict(PERCEPTION_PLUGIN_SPECS),
        },
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
        "mapper_spec": PERCEPTION_MAPPER_SPEC,
        "mapper_config": {
            "plugins": ["frame", "floor_plane", "motion_tracks"],
            "plugin_specs": dict(PERCEPTION_PLUGIN_SPECS),
        },
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
        "mapper_spec": PERCEPTION_MAPPER_SPEC,
        "mapper_config": {
            "plugins": ["frame", "floor_plane", "obstruction_tracks"],
            "plugin_specs": dict(PERCEPTION_PLUGIN_SPECS),
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
        },
        "output_contract": {
            "schema": PERCEPTION_TEXT_SCHEMA,
            "meaning": "structured frame, floor, and generic obstruction-track evidence",
        },
    },
}


def available_perception_algorithm_ids() -> tuple[str, ...]:
    return tuple(sorted(PERCEPTION_ALGORITHMS))
