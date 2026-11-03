"""Viewer lifecycle facts for commands and browser notices."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from ...shared.browser_result import BrowserOpenResult


@dataclass(frozen=True)
class ViewerEvent:
    host_name: str
    base_url: str
    not_live: str | None
    browser: BrowserOpenResult


@dataclass(frozen=True)
class ViewerResult:
    exit_code: int
    kind: Literal["error", "unknown_host", "unavailable", "interrupted"]
    error: str = ""
    host_name: str | None = None
    base_url: str | None = None
    hosts: list[dict[str, Any]] | None = None
