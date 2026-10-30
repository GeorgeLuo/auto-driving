"""Replay notices that cross from the workbench operation to its command."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WorkbenchEvent:
    """A runner snapshot to format, or a line the command prints as given.

    ``line`` is an operational notice, such as the browser opener's result.
    """

    state: dict[str, Any] | None = None
    server_url: str | None = None
    serving: bool = False
    line: str | None = None
