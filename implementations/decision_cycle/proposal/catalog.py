"""Packaged proposal plugins."""

from __future__ import annotations

from typing import Any

# Each plugin declares its own ID (its ``plugin_id``); entries do not repeat it.
PROPOSAL_PLUGINS: tuple[dict[str, Any], ...] = (
    {
        "spec": "implementations.decision_cycle.proposal.plugins.drive_forward.plugin:DriveForward",
        "description": "Propose constant forward throttle and zero steering, regardless of obstacles.",
        "default_config": {"throttle": 0.60},
    },
    {
        "spec": "implementations.decision_cycle.proposal.plugins.drive_backwards.plugin:DriveBackwards",
        "description": "Propose constant reverse throttle and zero steering, regardless of obstacles.",
        "default_config": {"throttle": -0.60},
    },
    {
        "spec": (
            "implementations.decision_cycle.proposal.plugins.avoid_recent_obstruction.plugin:"
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
