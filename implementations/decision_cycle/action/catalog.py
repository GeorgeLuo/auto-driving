"""Packaged action plugins; selected delegates movement authority to the runtime."""

from __future__ import annotations

from typing import Any

# Each plugin declares its own ID (its ``plugin_id``); entries do not repeat it.
ACTION_PLUGINS: tuple[dict[str, Any], ...] = (
    {
        "spec": "autonomy.decision_cycle.action.selected:SelectedAction",
        "description": "Authorize the selected plan; shared runtime owns movement authority.",
        "default_config": {},
    },
    {
        "spec": "autonomy.decision_cycle.action.hold:HoldAction",
        "description": "Record the selected command and always authorize idle control.",
        "default_config": {},
    },
    {
        "spec": "implementations.decision_cycle.action.mode.plugin:ModeAction",
        "description": (
            "Apply the selected command in a live drive mode (autonomy, local); "
            "otherwise authorize idle control."
        ),
        "default_config": {},
    },
)
DEFAULT_ACTION_PLUGINS: tuple[str, ...] = ("selected",)
