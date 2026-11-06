"""Live memory selected from the host's completed frame report."""

from __future__ import annotations

from typing import Any, Callable

from autonomy.decision_cycle.timing import step_timing

from autonomy.runtime.frame_loop import LATEST_JSON_PATH
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

    def probe() -> tuple[dict[str, Any], Any]:
        publication, fetch_error = (view.read_observation() if view is not None
                                    else read_publication(base_url, timeout_s=timeout_s))
        if view is not None:
            view.publish(publication)
        live = _probe_memory(
            host_name=host_name, base_url=base_url, publication=publication, fetch_error=fetch_error,
        )
        source = MemoryPublication(live.get("record"), live.get("error")) if include_publication else None
        return live, source

    def outbound(event: StreamEvent) -> None:
        emit(with_view(view, event, host_name=host_name))

    try:
        return poll_events(step="memory", probe=probe, refresh_s=refresh_s, once=once, emit=outbound)
    finally:
        if view is not None:
            view.close()


def probe_live_memory(*, host_name: str, timeout_s: float = 3.0) -> dict[str, Any]:
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
    *, host_name: str, base_url: str, publication: dict[str, Any] | None, fetch_error: str | None,
) -> dict[str, Any]:
    """Select memory from the same completed report the other step commands read."""
    probe = {
        "schema": MEMORY_LIVE_SCHEMA, "host_name": host_name,
        "endpoint": f"{base_url}{LATEST_JSON_PATH}", "probed_at_ms": timestamp_ms(),
    }
    if fetch_error is not None:
        return {**probe, "status": "error", "error": fetch_error}
    try:
        frame = read_frame_publication(publication, host_name=host_name, now_ms=timestamp_ms())
    except FramePublicationError as exc:
        return {**probe, "status": "unavailable", "error": exc.message_text}
    report = frame["report"]
    memory = report.cycle["memory"]
    probe.update(
        frame_id=report.frame_id, generation_id=report.generation_id,
        mode=report.application.mode, record=memory,
    )
    unavailable = step_unavailable(publication, "memory")
    if unavailable is not None:
        return {**probe, "status": unavailable[0], "error": unavailable[1]}
    if memory is None:
        return {**probe, "status": "absent", "error": absent_step_error("memory", host_name)}
    activation = report.values["activation"]["steps"].get("memory") or {}
    entry = step_timing(report.values.get("timing"), "memory")
    return {
        **probe, "status": "live", "health": publication.get("health"),
        "activation": activation,
        "plugin_ids": [item["plugin_id"] for item in memory.get("plugins", [])],
        "plugins": _probe_plugins(memory), "evidence_publisher": evidence_publisher(memory),
        "last_duration_ms": entry.get("duration_ms") if entry is not None else None,
    }


def _probe_plugins(status: dict[str, Any]) -> list[dict[str, Any]]:
    """Each applied plugin's step status with its ledger summary beside it.

    An entry keeps the step's per-plugin status (counters, ``last_error``,
    ``state``) and adds ``epoch_id``, ``health``, ``bounds`` and
    ``record_count`` from that plugin's state, None where the plugin reports
    none.
    """

    plugins = status.get("plugins")
    return [
        {
            **entry,
            **ledger_summary(entry.get("state") if isinstance(entry.get("state"), dict) else None),
        }
        for entry in (plugins if isinstance(plugins, list) else [])
        if isinstance(entry, dict) and isinstance(entry.get("plugin_id"), str)
    ]
