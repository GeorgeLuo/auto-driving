"""Perception presets: named, ordered selections of catalog plugins.

A preset lists the plugins to run and config overrides for some of them. The
default is a preset. An activation built from one records the preset name in
its ``preset`` metadata key.
"""

from __future__ import annotations

from typing import Any


DEFAULT_PERCEPTION_PRESET = "lightweight_observer"

PERCEPTION_PRESETS: dict[str, dict[str, Any]] = {
    "lightweight_observer": {
        "description": (
            "Lightweight perception: frame facts, visible floor, and "
            "first-hit floor boundaries."
        ),
        "plugins": ["frame", "floor_plane"],
    },
    "sim_debug": {
        "description": (
            "Simulator-only debug control: frame facts plus known Chase "
            "color-target signals."
        ),
        "plugins": ["frame", "sim_color_targets"],
    },
    "visual_observer": {
        "description": (
            "Generic visual observer: frame facts, floor/traversability, and "
            "bounded scene tracks."
        ),
        "plugins": ["frame", "floor_plane", "motion_tracks"],
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
    },
}


def available_perception_preset_ids() -> tuple[str, ...]:
    return tuple(sorted(PERCEPTION_PRESETS))


DEFAULT_PERCEPTION_PLUGINS: tuple[str, ...] = tuple(
    PERCEPTION_PRESETS[DEFAULT_PERCEPTION_PRESET]["plugins"]
)
