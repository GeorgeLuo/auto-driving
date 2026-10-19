"""Packaged plan plugins."""

from __future__ import annotations

from typing import Any

# Each plugin declares its own ID (its ``plugin_id``); entries do not repeat it.
PLAN_PLUGINS: tuple[dict[str, Any], ...] = (
    {
        "spec": "implementations.decision_cycle.plan.plugins.select_proposal.plugin:SelectProposal",
        "description": (
            "Select only a named proposal plugin's active candidate, or an alternate one "
            "while the current view is static; otherwise idle."
        ),
        "default_config": {"proposal_plugin_id": "drive_forward"},
    },
    {
        "spec": (
            "implementations.decision_cycle.plan.plugins.highest_confidence.plugin:"
            "HighestConfidencePlan"
        ),
        "description": "Select the most confident active candidate; otherwise plan idle.",
        "default_config": {},
    },
)
