"""Facts a plugin stream hands its command. No terminal text and no JSON."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StreamEvent:
    """One probe and the facts a screen needs.

    ``live`` is the public probe. ``source`` is the publication or other
    material the screen shows beside it. ``view`` is set only for a running
    terminal stream.
    """

    step: str
    live: dict[str, Any]
    source: Any = None
    clear: bool = False
    json_output: bool = False
    view: Any = None
    host_name: str = ""
    base_url: str = ""


@dataclass(frozen=True)
class MemoryPublication:
    """The memory report a stream read, or why that read failed."""

    report: Any = None
    error: str | None = None


@dataclass(frozen=True)
class StreamResult:
    """How a stream ended.

    ``message`` is a user-visible sentence for a preflight or a non-live
    one-shot probe. A rendered frame is not stored here; the command renders
    each ``StreamEvent`` as it arrives. ``preflight`` failed before polling.
    """

    exit_code: int
    message: str = ""
    live: dict[str, Any] | None = None
    preflight: bool = False
