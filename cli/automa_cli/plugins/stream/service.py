"""Dispatch a plugin stream to the step that knows how to read it."""

from __future__ import annotations

from collections.abc import Callable

from .decision import stream_decision
from .memory import stream_memory
from .models import StreamEvent, StreamResult
from .perception import stream_perception


def stream_host(
    step: str,
    *,
    host_name: str,
    refresh_s: float,
    once: bool,
    timeout_s: float,
    serve_view: bool,
    include_publication: bool,
    emit: Callable[[StreamEvent], None],
) -> StreamResult:
    """Poll ``step`` on ``host_name`` and emit one event per probe.

    ``serve_view`` opens the local runtime view. ``include_publication`` asks
    memory for the publication details its text shows. Perception always reads
    its publication, because that publication is the probe. Neither flag selects
    JSON or clears the terminal.
    """

    if step == "perception":
        return stream_perception(
            host_name=host_name, refresh_s=refresh_s, once=once, timeout_s=timeout_s,
            serve_view=serve_view, emit=emit,
        )
    if step == "memory":
        return stream_memory(
            host_name=host_name, refresh_s=refresh_s, once=once, timeout_s=timeout_s,
            serve_view=serve_view, include_publication=include_publication, emit=emit,
        )
    return stream_decision(
        step, host_name=host_name, refresh_s=refresh_s, once=once, timeout_s=timeout_s,
        serve_view=serve_view, emit=emit,
    )
