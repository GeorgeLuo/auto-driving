"""Packaged memory plugins."""

from __future__ import annotations

from typing import Any

_BOUNDED_EVIDENCE_CONFIG: dict[str, Any] = {
    "max_records": 32,
    "max_age_ms": 10_000,
    "eviction_policy": "oldest_first",
    "min_confidence": 0.0,
    "retain_things": True,
    "retain_signals": True,
    "max_property_bytes": 4_096,
    "max_serialized_bytes": 262_144,
}

# Each plugin declares its own ID (its ``plugin_id``); entries do not repeat it.
MEMORY_PLUGINS: tuple[dict[str, Any], ...] = (
    {
        "spec": (
            "implementations.decision_cycle.memory.plugins.bounded_evidence.plugin:BoundedEvidenceLedger"
        ),
        "description": (
            "Bounded recency ledger of observation things and signals with "
            "origin, age expiry, and oldest-first eviction. Does not claim "
            "semantic object identity or world truth."
        ),
        "default_config": _BOUNDED_EVIDENCE_CONFIG,
    },
    {
        "spec": "implementations.decision_cycle.memory.plugins.stop_row.plugin:StopRowHypothesis",
        "description": (
            "Learn the image row at which to start avoiding an obstacle ahead: "
            "a collision starts the hypothesis, and each stop that stays short "
            "of the image bottom or comes too close bisects it."
        ),
        "default_config": {
            "min_stop_row": 0.6, "blind_row": 0.98, "resolution": 0.02, "settle_ms": 1000,
            "obstacle_signal_ids": ["obstacle_in_sight", "wall_in_sight"],
        },
    },
    {
        "spec": "implementations.decision_cycle.memory.plugins.turn_model.plugin:TurnModel",
        "description": (
            "Learn how fast the car's own turn command moves tracked landmarks "
            "across the view, in image widths per second, and carry their "
            "bearings after they leave it. Changes no command."
        ),
        "default_config": {
            "landmark_plugin_id": "sim_cardboard_permanence", "sensor_id": "front_camera",
            "max_samples": 64, "max_interval_ms": 250, "edge_margin": 0.01, "max_size_change": 1.5,
            "stall_fraction": 0.25, "stall_intervals": 2, "forget_straight_ms": 2000,
            "reacquire_within": 0.15, "max_landmarks": 16,
        },
    },
    {
        "spec": "implementations.decision_cycle.memory.plugins.approach_model.plugin:ApproachModel",
        "description": (
            "Learn how many milliseconds a lower edge ahead spends on each image "
            "row while the car drives straight, and how long it drives straight "
            "before it has to stop. Changes no command."
        ),
        "default_config": {
            "edge_plugin_ids": ["sim_wall", "sim_cardboard_permanence"], "sensor_id": "front_camera",
            "min_x": 0.3, "max_x": 0.7, "row_bins": 120, "edge_margin": 0.01, "max_samples": 64,
            "max_interval_ms": 250, "stop_row_belief_id": "stop_row.row",
            "stop_reasons": ["avoid_back_up_until_right_clear"],
        },
    },
)
