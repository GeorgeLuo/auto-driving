"""Live memory selected from the host's completed frame report."""

from __future__ import annotations

from typing import Callable

from autonomy.serialization import frozen_mapping_to_dict
from autonomy.decision_cycle.memory.interface import MemoryReport
from collections.abc import Mapping
from autonomy.decision_cycle.timing import step_timing

from autonomy.runtime.frame_loop import FramePublication, LATEST_JSON_PATH
from ...hosts.publications.frame_publication import (
    FramePublicationError,
    read_frame_publication,
    step_unavailable,
)
from ...hosts.discovery import runtime_base_url
from ...hosts.step_activations import absent_step_error
from ...hosts.errors import RuntimeHostError
from ...shared.memory_report import evidence_publisher, ledger_summary
from ...shared.timestamps import timestamp_ms
from .models import MemoryPublication, StreamEvent, StreamResult
from .perception import read_publication
from .polling import poll_events, preflight_failure, unavailable_probe
from .session import StreamView, with_view

MEMORY_LIVE_SCHEMA = "host_memory_live"


def stream_memory(
    *,
    host_name: str,
    refresh_s: float,
    once: bool,
    timeout_s: float,
    serve_view: bool,
    include_publication: bool,
    emit: Callable[[StreamEvent], None],
) -> StreamResult:
    """Read one completed frame for memory's probe, text, and optional local view."""

    try:
        base_url = runtime_base_url(host_name)
    except RuntimeHostError as exc:
        return preflight_failure(
            message=str(exc),
            live=unavailable_probe(MEMORY_LIVE_SCHEMA, host_name, str(exc)),
        )
    view = StreamView(host_name, base_url=base_url, timeout_s=timeout_s) if serve_view else None

    def probe() -> tuple[dict[str, object], MemoryPublication | None]:
        publication, fetch_error = (view.read_observation() if view is not None
                                    else read_publication(base_url, timeout_s=timeout_s))
        if view is not None:
            view.publish(publication)
        live = _probe_memory(
            host_name=host_name, base_url=base_url, publication=publication, fetch_error=fetch_error,
        )
        source = MemoryPublication(None if publication is None or publication.report is None else publication.report.cycle.memory,
                                   live.get("error")) if include_publication else None
        return live, source

    def outbound(event: StreamEvent) -> None:
        emit(with_view(view, event, host_name=host_name))

    try:
        return poll_events(step="memory", probe=probe, refresh_s=refresh_s, once=once, emit=outbound)
    finally:
        if view is not None:
            view.close()


def probe_live_memory(*, host_name: str, timeout_s: float = 3.0) -> dict[str, object]:
    """Return a normalized live-memory probe without requiring stream mode."""

    try:
        base_url = runtime_base_url(host_name)
    except RuntimeHostError as exc:
        return unavailable_probe(MEMORY_LIVE_SCHEMA, host_name, str(exc))
    publication, error = read_publication(base_url, timeout_s=timeout_s)
    return _probe_memory(
        host_name=host_name, base_url=base_url, publication=publication, fetch_error=error,
    )


def _probe_memory(
    *, host_name: str, base_url: str, publication: FramePublication | None, fetch_error: str | None,
) -> dict[str, object]:
    """Select memory from the same completed report the other step commands read."""
    probe: dict[str, object] = {
        "schema": MEMORY_LIVE_SCHEMA, "host_name": host_name,
        "endpoint": f"{base_url}{LATEST_JSON_PATH}", "probed_at_ms": timestamp_ms(),
    }
    if fetch_error is not None or publication is None:
        return {**probe, "status": "error", "error": fetch_error or "No publication was read."}
    try:
        report = read_frame_publication(publication, host_name=host_name)
    except FramePublicationError as exc:
        return {**probe, "status": "unavailable", "error": exc.message_text}
    memory = report.cycle.memory
    probe.update(
        frame_id=report.frame_id, generation_id=report.generation_id,
        mode=report.application.mode, record=None if memory is None else memory.to_dict(),
    )
    unavailable = step_unavailable(publication, "memory")
    if unavailable is not None:
        return {**probe, "status": unavailable[0], "error": unavailable[1]}
    if memory is None:
        return {**probe, "status": "absent", "error": absent_step_error("memory", host_name)}
    activation = report.values.activation.steps.get("memory") or {}
    entry = step_timing(report.values.timing, "memory")
    return {
        **probe, "status": "live", "health": publication.health,
        "activation": activation,
        "plugin_ids": [item.plugin_id for item in memory.plugins],
        "plugins": _probe_plugins(memory), "evidence_publisher": evidence_publisher(memory),
        "last_duration_ms": entry.get("duration_ms") if entry is not None else None,
    }


def _probe_plugins(report: MemoryReport) -> list[dict[str, object]]:
    """Each applied plugin's JSON status with its ledger summary beside it."""
    return [
        {**plugin.to_dict(), **frozen_mapping_to_dict(ledger_summary(plugin.state if isinstance(plugin.state, Mapping) else None))}
        for plugin in report.plugins
    ]
