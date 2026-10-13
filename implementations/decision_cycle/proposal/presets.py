"""Proposal presets: named, ordered selections of catalog plugins.

A preset lists the plugins to run and may override config for some of them.
The default is a preset. An activation built from one records the preset name
in its ``preset`` metadata key; a selection that is not a preset records
``custom``.
"""

from __future__ import annotations

from typing import Any


DEFAULT_PROPOSAL_PRESET = "obstruction_avoidance"

PROPOSAL_PRESETS: dict[str, dict[str, Any]] = {
    "obstruction_avoidance": {
        "description": (
            "Propose steering away from lateral obstruction evidence retained "
            "in shared memory; otherwise propose nothing."
        ),
        "plugins": ["avoid_recent_obstruction"],
    },
}


def available_proposal_preset_ids() -> tuple[str, ...]:
    return tuple(sorted(PROPOSAL_PRESETS))


DEFAULT_PROPOSAL_PLUGINS: tuple[str, ...] = tuple(
    PROPOSAL_PRESETS[DEFAULT_PROPOSAL_PRESET]["plugins"]
)
