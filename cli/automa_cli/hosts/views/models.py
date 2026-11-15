"""Viewer and still facts for commands and browser notices."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

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
    hosts: list[dict[str, object]] | None = None


@dataclass(frozen=True)
class StillResult:
    exit_code: int
    kind: Literal["error", "unavailable", "saved", "missing"]
    error: str = ""
    host_name: str = ""
    base_url: str = ""
    frame_id: str = ""
    age_ms: int | None = None
    health: str = ""
    path: str = ""
    missing: str = ""
    browser: BrowserOpenResult | None = None
