"""Packaged proposal plugins and the proposal step's default selection."""

from __future__ import annotations

from typing import Any

from implementations.decision_cycle.proposal.avoid_recent_obstruction import plugin as avoid

PROPOSAL_PLUGINS: dict[str, dict[str, Any]] = {
    avoid.PLUGIN_ID: {
        "spec": avoid.PLUGIN_SPEC,
        "description": (
            "Steer away from lateral obstruction evidence retained in shared memory; "
            "otherwise propose nothing."
        ),
        "default_config": {
            "accepted_kinds": list(avoid.DEFAULT_ACCEPTED_KINDS),
            "retained_max_age_ms": avoid.DEFAULT_RETAINED_MAX_AGE_MS,
            "steer_magnitude": avoid.DEFAULT_STEER_MAGNITUDE,
        },
    },
}
DEFAULT_PROPOSAL_PLUGINS: tuple[str, ...] = (avoid.PLUGIN_ID,)
