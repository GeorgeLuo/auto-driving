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
}


def available_perception_algorithm_ids() -> tuple[str, ...]:
    return tuple(sorted(PERCEPTION_ALGORITHMS))
