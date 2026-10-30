"""Values that cross a run's process and module boundaries.

``WORKER_MODULE`` is the background entry point. Stop still recognizes a
process started as ``runs.automation`` or ``hosts.automation`` before that
entry point moved. The match is the exact module token, not a prefix.

``RunEvent`` carries live facts; ``RunResult`` describes the outcome. ``status_reason`` is the truncated reason status collection and
status text both use.
"""

from __future__ import annotations

from typing import Any
from dataclasses import dataclass
from pathlib import Path


WORKER_MODULE = "cli.automa_cli.hosts.runs.worker"
RECOGNIZED_WORKER_MODULES = (
    WORKER_MODULE,
    "cli.automa_cli.hosts.runs.automation",
    "cli.automa_cli.hosts.automation",
)

MAX_STATUS_REASON_CHARS = 240


@dataclass(frozen=True)
class RunEvent:
    kind: str
    data: dict[str, Any]


@dataclass(frozen=True)
class RunResult:
    exit_code: int
    message: str = ""
    state: dict[str, Any] | None = None
    state_path: Path | None = None


def run_exit_code(state: dict[str, Any]) -> int:
    if state["status"] == "error":
        return 2
    return 130 if state.get("stop_reason") == "keyboard_interrupt" else 0


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
