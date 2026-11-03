"""Browser launch facts and their shared display text; no browser access."""
from dataclasses import dataclass


@dataclass(frozen=True)
class BrowserOpenResult:
    url: str
    opened: bool
    error: str | None = None
