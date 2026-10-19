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
            "maneuver_key": "proposal.avoid_obstacle.maneuver", "stop_phase": "backing_up",
        },
    },
)
