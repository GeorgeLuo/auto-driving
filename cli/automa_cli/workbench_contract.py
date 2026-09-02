"""Shared identifiers and errors for the image replay workbench."""

from __future__ import annotations

from typing import Any


WORKBENCH_SEQUENCE_ID = "workbench.image_replay.v1"
WORKBENCH_STATE_SCHEMA = "workbench_image_replay_state_v1"
WORKBENCH_SERVER_SCHEMA = "workbench_server_v1"
WORKBENCH_ACTION_RESULT_SCHEMA = "workbench_action_result_v1"
WORKBENCH_ERROR_SCHEMA = "workbench_error_v1"
WORKBENCH_HOST = "127.0.0.1"
WORKBENCH_DEFAULT_CADENCE_MS = 250
WORKBENCH_DEFAULT_PACE = "fixed"
WORKBENCH_DEFAULT_LOOP = True
WORKBENCH_PACES = ("fixed", "realtime")
WORKBENCH_MAX_ACTION_BYTES = 64 * 1024
WORKBENCH_ACTIONS = (
    "validate",
    "refresh_plugins",
    "inspect_plugins",
    "select_plugins",
    "set_plugins",
    "start",
    "pause",
    "resume",
    "step",
    "seek",
    "cancel",
    "reset",
    "set_cadence",
    "set_loop",
)


class ReplayActionError(ValueError):
    """A structured action or lifecycle boundary failure."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 409,
        boundary: str = "action",
        state: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.boundary = boundary
        self.state = state


__all__ = [
    "ReplayActionError",
    "WORKBENCH_ACTIONS",
    "WORKBENCH_ACTION_RESULT_SCHEMA",
    "WORKBENCH_DEFAULT_CADENCE_MS",
    "WORKBENCH_DEFAULT_LOOP",
    "WORKBENCH_DEFAULT_PACE",
    "WORKBENCH_ERROR_SCHEMA",
    "WORKBENCH_HOST",
    "WORKBENCH_MAX_ACTION_BYTES",
    "WORKBENCH_PACES",
    "WORKBENCH_SEQUENCE_ID",
    "WORKBENCH_SERVER_SCHEMA",
    "WORKBENCH_STATE_SCHEMA",
]
