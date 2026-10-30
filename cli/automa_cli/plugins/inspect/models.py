"""The report an inspection returns. No terminal text and no JSON."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class InspectResult:
    """A finished inspection.

    ``report`` is set only when every requested step ran. ``error`` is the
    sentence a failure shows, and a failure writes nothing.
    """

    exit_code: int
    error: str | None = None
    report: dict[str, Any] | None = None
