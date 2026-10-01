"""Packaged action plugins: the core hold plugin and the mode-gated plugin."""

from __future__ import annotations

from typing import Any

from autonomy.decision_cycle.action import hold
from implementations.decision_cycle.action.mode import plugin as mode

ACTION_PLUGINS: dict[str, dict[str, Any]] = {
    hold.PLUGIN_ID: {
        "spec": hold.PLUGIN_SPEC,
        "description": "Record the selected command and always authorize idle control.",
        "default_config": {},
    },
    mode.PLUGIN_ID: {
        "spec": mode.PLUGIN_SPEC,
        "description": (
            "Apply the selected command in a live drive mode "
            f"({', '.join(sorted(mode.LIVE_MODES))}); otherwise authorize idle control."
        ),
        "default_config": {},
    },
}
DEFAULT_ACTION_PLUGINS: tuple[str, ...] = (hold.PLUGIN_ID,)
