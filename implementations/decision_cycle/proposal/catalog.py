"""Packaged proposal plugins and the proposal step's default selection."""

from __future__ import annotations

from typing import Any

# Each plugin declares its own ID (its ``plugin_id``); entries do not repeat it.
PROPOSAL_PLUGINS: tuple[dict[str, Any], ...] = (
    {
        "spec": (
            "implementations.decision_cycle.proposal.avoid_recent_obstruction.plugin:"
            "AvoidRecentObstruction"
        ),
        "description": (
            "Steer away from lateral obstruction evidence retained in shared memory; "
            "otherwise propose nothing."
        ),
        "default_config": {
            "accepted_kinds": ["floor_boundary", "obstacle", "obstruction_evidence"],
            "retained_max_age_ms": 1000,
            "steer_magnitude": 1.0,
        },
    },
)
DEFAULT_PROPOSAL_PLUGINS: tuple[str, ...] = ("avoid_recent_obstruction",)
