"""Facts a plugin stream hands its command. No terminal text and no JSON."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ViewState:
    """URLs and errors for one local view.

    A renderer prints these. It does not receive the server or the feed.
    """

    url: str | None = None
    error: str | None = None
    decision_page: str | None = None
    decision_error: str | None = None


@dataclass(frozen=True)
class PerceptionPublication:
    """What the perception screen shows from the host's latest observation publication.

    The stream reads these fields when the publication arrives; every field is
    ``None``, and ``text`` empty, when no publication was read. ``text`` is the
    perception text the publication carries, or a summary of its payload.
    """

    health: Any = None
    presets: Any = None
    mode: Any = None
    steering: Any = None
    throttle: Any = None
    reason: Any = None
    processed: Any = None
    cycle_ms: Any = None
    age_ms: Any = None
    frame_id: Any = None
    captured_at_ms: Any = None
    signal_count: int | None = None
    thing_count: int | None = None
    text: str = ""


@dataclass(frozen=True)
class StreamEvent:
    """One probe and the facts a screen needs.

    ``live`` is the public probe. ``source`` is what the screen shows beside
    it: perception's publication or memory's report. ``view`` is set only while
    a local view is being served, and it carries that view's addresses rather
    than the view itself.
    """

    step: str
    live: dict[str, Any]
    source: PerceptionPublication | MemoryPublication | None = None
    view: ViewState | None = None
    host_name: str = ""
    base_url: str = ""


@dataclass(frozen=True)
class MemoryPublication:
    """The memory report a stream read, or why that read failed.

    The report is the memory step's own document, which ``shared.memory_report``
    reads for every renderer.
    """

    report: dict[str, Any] | None = None
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
