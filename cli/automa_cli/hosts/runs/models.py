"""Values that cross a run's process and module boundaries.

``WORKER_MODULE`` is the background entry point. Stop still recognizes a
process started as ``runs.automation`` or ``hosts.automation`` before that
entry point moved. The match is the exact module token, not a prefix.

``RunEvent`` carries live facts; ``RunResult`` describes the outcome. ``status_reason`` is the truncated reason status collection and
status text both use.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from ...shared.browser_result import BrowserOpenResult

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
    problems: list[dict[str, str]] | None = None


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


@dataclass(frozen=True)
class StartResult:
    exit_code: int
    kind: Literal[
        "error",
        "activation_invalid",
        "starting",
        "records_disagree",
        "authority_mismatch",
        "view_unready",
        "existing",
        "launch_failed",
        "started",
    ]
    host_name: str = ""
    error: str = ""
    pid: int | None = None
    state_pid: int | None = None
    state_path: Path | None = None
    log_path: Path | None = None
    expected_authority: dict[str, Any] | None = None
    actual_authority: dict[str, Any] | None = None
    view: dict[str, Any] | None = None
    process_record: dict[str, Any] | None = None
    startup: dict[str, Any] | None = None
    browser: BrowserOpenResult | None = None
    problems: list[dict[str, str]] | None = None


@dataclass(frozen=True)
class StopResult:
    exit_code: int
    kind: Literal[
        "error",
        "remote_failed",
        "no_pid",
        "already_dead",
        "stale_pid",
        "stopped",
        "forced",
        "timeout",
        "release_failed",
    ]
    host_name: str
    pid: int | None = None
    state_path: Path | None = None
    command: str | None = None
    error: str = ""


@dataclass(frozen=True)
class RestartResult:
    exit_code: int
    kind: Literal["error", "stop_failed", "restart_failed", "started"] = "started"
    host_name: str = ""
    stop: StopResult | None = None
    start: StartResult | None = None
    error: str = ""
    problems: list[dict[str, str]] | None = None
