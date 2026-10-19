"""Plan presets: named, ordered selections of catalog plugins.

A preset lists the plugins to run and may override config for some of them.
The default is a preset. An activation built from one records the preset name
in its ``preset`` metadata key; a selection that is not a preset records
``custom``.
"""

from __future__ import annotations

from typing import Any


DEFAULT_PLAN_PRESET = "most_confident"

PLAN_PRESETS: dict[str, dict[str, Any]] = {
    "chase-v1-detect-by-color": {
        "description": "Chase v1 baseline: select only the drive_forward proposal.",
        "plugins": ["select_proposal"],
        "plugin_configs": {"select_proposal": {"proposal_plugin_id": "drive_forward"}},
    },
    "most_confident": {
        "description": "Select the most confident active candidate; otherwise plan idle.",
        "plugins": ["highest_confidence"],
    },
}


def available_plan_preset_ids() -> tuple[str, ...]:
    return tuple(sorted(PLAN_PRESETS))


DEFAULT_PLAN_PLUGINS: tuple[str, ...] = tuple(
    PLAN_PRESETS[DEFAULT_PLAN_PRESET]["plugins"]
)
