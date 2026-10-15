"""Packaged plan plugins."""

from __future__ import annotations

from typing import Any

# Each plugin declares its own ID (its ``plugin_id``); entries do not repeat it.
PLAN_PLUGINS: tuple[dict[str, Any], ...] = (
    {
        "spec": (
            "implementations.decision_cycle.plan.plugins.highest_confidence.plugin:"
            "HighestConfidencePlan"
        ),
        "description": "Select the most confident active candidate; otherwise plan idle.",
        "default_config": {},
    },
)
