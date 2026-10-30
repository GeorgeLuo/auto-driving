"""Replay notices that cross from the workbench operation to its command."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..shared.browser_result import BrowserOpenResult


@dataclass(frozen=True)
class WorkbenchEvent:
    """Runner state or a browser opening outcome for command presentation."""

    state: dict[str, Any] | None = None
    server_url: str | None = None
    serving: bool = False
    browser: BrowserOpenResult | None = None


@dataclass(frozen=True)
class WorkbenchResult:
    exit_code: int
    error: str | None = None
    viewer_stopped: bool = False
