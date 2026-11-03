"""The refresh loop shared by every step stream."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from ...shared.timestamps import timestamp_ms
from .models import StreamEvent, StreamResult


def unavailable_probe(schema: str, host_name: str, error: str) -> dict[str, Any]:
    return {
        "schema": schema,
        "host_name": host_name,
        "status": "unavailable",
        "error": error,
        "probed_at_ms": timestamp_ms(),
    }


def preflight_failure(*, message: str, live: dict[str, Any]) -> StreamResult:
    """A stream that cannot start polling. Exit 2, in text or JSON."""

    return StreamResult(exit_code=2, message=message, live=live, preflight=True)


def poll_events(
    *,
    step: str,
    probe: Callable[[], tuple[dict[str, Any], Any]],
    refresh_s: float,
    once: bool,
    no_clear: bool,
    json_output: bool,
    emit: Callable[[StreamEvent], None],
) -> StreamResult:
    """Probe, emit one event, and sleep until ``once`` or the caller stops it.

    ``once`` exits 0 only when the probe is live. A keyboard interrupt exits 130
    with an empty message. The caller renders and writes each event.
    """

    try:
        while True:
            live, source = probe()
            emit(StreamEvent(
                step=step,
                live=live,
                source=source,
                clear=not (no_clear or once or json_output),
                json_output=json_output,
            ))
            if once:
                status = str(live.get("status") or "unknown")
                if status == "live":
                    return StreamResult(exit_code=0, live=live)
                diagnostic = str(live.get("error") or f"{step} stream is not live (status={status})")
                return StreamResult(exit_code=2, message=diagnostic, live=live)
            time.sleep(max(0.1, float(refresh_s)))
    except KeyboardInterrupt:
        return StreamResult(exit_code=130)
