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
    no_clear: bool,
    json_output: bool,
    timeout_s: float,
    emit: Callable[[StreamEvent], None],
) -> StreamResult:
    """Poll ``step`` on ``host_name`` and emit one event per probe."""

    if step == "perception":
        return stream_perception(
            host_name=host_name, refresh_s=refresh_s, once=once, no_clear=no_clear,
            json_output=json_output, timeout_s=timeout_s, emit=emit,
        )
    if step == "memory":
        return stream_memory(
            host_name=host_name, refresh_s=refresh_s, once=once, no_clear=no_clear,
            json_output=json_output, timeout_s=timeout_s, emit=emit,
        )
    return stream_decision(
        step, host_name=host_name, refresh_s=refresh_s, once=once, no_clear=no_clear,
        json_output=json_output, timeout_s=timeout_s, emit=emit,
    )
