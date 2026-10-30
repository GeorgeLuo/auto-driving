"""Live memory: the memory step the host reports, plus its latest publication."""

from __future__ import annotations

from typing import Any, Callable

from autonomy.decision_cycle.timing import step_timing

from ...hosts.publications.host_publications import fetch_autonomy_status
from ...hosts.discovery import runtime_base_url
from ...hosts.step_activations import absent_step_error
from ...releases.errors import RuntimeHostError
from ...shared.memory_report import evidence_publisher, ledger_summary
from ...shared.timestamps import timestamp_ms
from .models import MemoryPublication, StreamEvent, StreamResult
from .perception import read_publication
from .polling import poll_events, preflight_failure, unavailable_probe
from .session import StreamView, with_snapshot

MEMORY_LIVE_SCHEMA = "host_memory_live"
LIVE_STEP_SCHEMAS = {
    "memory": MEMORY_LIVE_SCHEMA,
    **{step: f"host_{step}_live" for step in ("proposal", "plan", "action")},
}


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
    """Poll the live memory step the host runs.

    ``live`` means the host runs a memory step; update health stays in the
    plugin diagnostics. ``include_publication`` also reads the latest published
    memory report for the text screen. ``serve_view`` feeds a local view from
    that same read. JSON leaves the publication unread.
    """

    try:
        base_url = runtime_base_url(host_name)
    except RuntimeHostError as exc:
        return preflight_failure(
            message=str(exc),
            live=unavailable_probe(MEMORY_LIVE_SCHEMA, host_name, str(exc)),
        )
    view = StreamView(host_name, base_url=base_url, timeout_s=timeout_s) if serve_view else None

    def probe() -> tuple[dict[str, Any], Any]:
        live = _probe_step("memory", host_name=host_name, base_url=base_url, timeout_s=timeout_s)
        source = None
        if include_publication or view is not None:
            publication, fetch_error = (view.read_observation() if view is not None
                                        else read_publication(base_url, timeout_s=timeout_s))
            if view is not None:
                view.publish(publication)
            if include_publication:
                report = publication.get("memory") if isinstance(publication, dict) else None
                source = MemoryPublication(report, fetch_error)
        return live, source

    def outbound(event: StreamEvent) -> None:
        emit(with_snapshot(view, event, host_name=host_name))

    try:
        return poll_events(step="memory", probe=probe, refresh_s=refresh_s, once=once, emit=outbound)
    finally:
        if view is not None:
            view.close()


def probe_live_memory(*, host_name: str, timeout_s: float = 3.0) -> dict[str, Any]:
    """Return a normalized live-memory probe without requiring stream mode."""

    return probe_live_step("memory", host_name=host_name, timeout_s=timeout_s)


def probe_live_step(step: str, *, host_name: str, timeout_s: float = 3.0) -> dict[str, Any]:
    """``step`` as the host runs it: its runner's status
    under ``/host/status``, not what any view last rendered."""

    try:
        base_url = runtime_base_url(host_name)
    except RuntimeHostError as exc:
        return unavailable_probe(LIVE_STEP_SCHEMAS[step], host_name, str(exc))
    return _probe_step(step, host_name=host_name, base_url=base_url, timeout_s=timeout_s)


def _probe_step(step: str, *, host_name: str, base_url: str, timeout_s: float) -> dict[str, Any]:
    probe: dict[str, Any] = {
        "schema": LIVE_STEP_SCHEMAS[step],
        "host_name": host_name,
        "endpoint": f"{base_url}/host/status",
        "probed_at_ms": timestamp_ms(),
    }
    try:
        status = fetch_autonomy_status(base_url, timeout_s=timeout_s)
    except ConnectionError as exc:
        return {**probe, "status": "error", "error": str(exc)}

    session = status.get("session") if isinstance(status.get("session"), dict) else {}
    probe.update(mode=status.get("mode"), session_status=session.get("status"))
    autonomy = status.get("host") if isinstance(status.get("host"), dict) else {}
    steps = autonomy.get("steps") if isinstance(autonomy.get("steps"), dict) else {}
    runner = steps.get(step) if isinstance(steps.get(step), dict) else None
    if runner is None:
        return {**probe, "status": "absent", "error": absent_step_error(step, host_name)}
    fields = _live_step_fields(step, runner)
    # The host's cycle times each step call; the runner keeps only its
    # plugins' times.
    last_cycle = autonomy.get("last_cycle") if isinstance(autonomy.get("last_cycle"), dict) else {}
    entry = step_timing(last_cycle.get("timing"), step)
    fields["last_duration_ms"] = entry.get("duration_ms") if entry is not None else None
    return {**probe, "status": "live", **fields}


def _live_step_fields(step: str, status: dict[str, Any]) -> dict[str, Any]:
    """The runner status fields a live probe reports; memory adds its ledgers."""

    fields = {
        "activation": status.get("activation"),
        "plugin_ids": status.get("plugin_ids", []),
        "selected_plugin_ids": status.get("selected_plugin_ids", []),
        "plugin_report": status.get("plugin_report"),
        "last_error": status.get("last_error"),
        "failure_count": status.get("failure_count"),
    }
    if step == "memory":
        fields.update(
            plugins=_probe_plugins(status),
            evidence_publisher=evidence_publisher(status),
            update_count=status.get("update_count"),
            reset_count=status.get("reset_count"),
        )
    else:
        fields["run_count"] = status.get("run_count")
    return fields


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
