"""Values that cross a run's process and module boundaries.

``WORKER_MODULE`` is the background entry point. Stop still recognizes a
process started as ``runs.automation`` or ``hosts.automation`` before that
entry point moved. The match is the exact module token, not a prefix.

``RunTranscript`` is the live output the monitor updates and a renderer
implements. ``status_reason`` is the truncated reason status collection and
status text both use.
"""

from __future__ import annotations

from typing import Any, Protocol


WORKER_MODULE = "cli.automa_cli.hosts.runs.worker"
RECOGNIZED_WORKER_MODULES = (
    WORKER_MODULE,
    "cli.automa_cli.hosts.runs.automation",
    "cli.automa_cli.hosts.automation",
)

MAX_STATUS_REASON_CHARS = 240


class RunTranscript(Protocol):
    """Live lines during a run, and the closing result the caller prints."""

    def startup(self, state: dict[str, Any]) -> None:
        """Show that the run has started."""

    def frame(
        self, frame_id: str, *, signals: int, things: int, action: Any, count: int, verbose: bool,
    ) -> None:
        """Show one captured frame when the transcript includes that frame."""

    def result(self, state: dict[str, Any], *, state_path: str) -> tuple[int, str]:
        """Return the exit code and closing lines. ``state_path`` is display text."""


def status_reason(value: Any, *, fallback: str) -> str:
    """The first line of a reason, truncated so a status row stays one line."""

    lines = (
        [line.strip() for line in value.splitlines() if line.strip()]
        if isinstance(value, str)
        else []
    )
    reason = lines[0] if lines else fallback
    if len(reason) <= MAX_STATUS_REASON_CHARS:
        return reason
    return f"{reason[:MAX_STATUS_REASON_CHARS]}..."
