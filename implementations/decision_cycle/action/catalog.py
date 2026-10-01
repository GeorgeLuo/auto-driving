"""Packaged action plugins: the core hold plugin and the mode-gated plugin."""

from __future__ import annotations

from typing import Any

ACTION_PLUGINS: dict[str, dict[str, Any]] = {
    "hold": {
        "spec": "autonomy.decision_cycle.action.hold:HoldAction",
        "description": "Record the selected command and always authorize idle control.",
        "default_config": {},
    },
    "mode": {
        "spec": "implementations.decision_cycle.action.mode.plugin:ModeAction",
        "description": (
            "Apply the selected command in a live drive mode (autonomy, local); "
            "otherwise authorize idle control."
        ),
        "default_config": {},
    },
}
DEFAULT_ACTION_PLUGINS: tuple[str, ...] = ("hold",)
