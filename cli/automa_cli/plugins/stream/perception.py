"""Live perception: the host's latest observation publication."""

from __future__ import annotations

from typing import Any, Callable

from autonomy.runtime.frame_loop import LATEST_JSON_PATH

from ...hosts.publications.frame_publication import (
    perception_duration_ms,
    perception_text_from_publication,
    publication_presets,
    step_unavailable,
)
from ...hosts.publications.host_publications import fetch_frame_publication
from ...hosts.discovery import runtime_base_url
from ...hosts.errors import RuntimeHostError
from ...shared.timestamps import timestamp_ms
from .models import PerceptionPublication, StreamEvent, StreamResult
from .polling import poll_events, preflight_failure, unavailable_probe
from .session import StreamView, with_view

PERCEPTION_LIVE_SCHEMA = "host_perception_live"


def read_publication(base_url: str, *, timeout_s: float) -> tuple[dict[str, Any] | None, str | None]:
    try:
        return fetch_frame_publication(base_url, timeout_s=timeout_s), None
    except ConnectionError as exc:
        return None, str(exc)


def stream_perception(
    *,
    host_name: str,
    refresh_s: float,
    once: bool,
    timeout_s: float,
    serve_view: bool,
    emit: Callable[[StreamEvent], None],
) -> StreamResult:
    """Poll the latest perception the host publishes.

    The probe is the observation publication, with or without a local view.
    ``serve_view`` feeds a local runtime view from that same read. The command
    asks for a view only on a continuous text stream.
    """

    try:
        base_url = runtime_base_url(host_name)
    except RuntimeHostError as exc:
        return preflight_failure(
            message=str(exc),
            live=unavailable_probe(PERCEPTION_LIVE_SCHEMA, host_name, str(exc)),
        )
    view = StreamView(host_name, base_url=base_url, timeout_s=timeout_s) if serve_view else None

    def probe() -> tuple[dict[str, Any], PerceptionPublication]:
        publication, fetch_error = (view.read_observation() if view is not None
                                    else read_publication(base_url, timeout_s=timeout_s))
        if view is not None:
            view.publish(publication)
        live = _probe_perception(
            host_name=host_name, base_url=base_url, publication=publication, fetch_error=fetch_error,
        )
        return live, perception_publication(publication)

    def outbound(event: StreamEvent) -> None:
        emit(with_view(view, event, host_name=host_name, base_url=base_url))

    try:
        return poll_events(
            step="perception", probe=probe, refresh_s=refresh_s, once=once, emit=outbound,
        )
    finally:
        if view is not None:
            view.close()


def perception_publication(publication: dict[str, Any] | None) -> PerceptionPublication:
    """The fields the perception screen shows, read from the host's observation publication."""

    if not isinstance(publication, dict):
        return PerceptionPublication()
    report = publication.get("report") if isinstance(publication.get("report"), dict) else {}
    cycle = report.get("cycle") if isinstance(report.get("cycle"), dict) else {}
    perception = cycle.get("perception") if isinstance(cycle.get("perception"), dict) else {}
    application = report.get("application") if isinstance(report.get("application"), dict) else {}
    values = report.get("values") if isinstance(report.get("values"), dict) else {}
    timing = values.get("timing") if isinstance(values.get("timing"), dict) else {}
    signals, things = perception.get("signals"), perception.get("things")
    return PerceptionPublication(
        health=publication.get("health"),
        presets=publication_presets(publication),
        mode=application.get("mode"),
        steering=application.get("steering"),
        throttle=application.get("throttle"),
        reason=application.get("reason"),
        cycle_ms=timing.get("duration_ms"),
        age_ms=publication.get("result_age_ms"),
        frame_id=report.get("frame_id"),
        captured_at_ms=report.get("timestamp_ms"),
        signal_count=len(signals) if isinstance(signals, list) else None,
        thing_count=len(things) if isinstance(things, list) else None,
        text=perception_text_from_publication(publication) if publication else "",
    )


def _probe_perception(
    *,
    host_name: str,
    base_url: str,
    publication: dict[str, Any] | None,
    fetch_error: str | None,
) -> dict[str, Any]:
    probe: dict[str, Any] = {
        "schema": PERCEPTION_LIVE_SCHEMA,
        "host_name": host_name,
        "endpoint": f"{base_url}{LATEST_JSON_PATH}",
        "probed_at_ms": timestamp_ms(),
    }
    if fetch_error is not None or publication is None:
        return {**probe, "status": "error", "error": fetch_error or "No publication was read."}

    health = str(publication.get("health") or "unknown")
    report = publication.get("report") if isinstance(publication.get("report"), dict) else {}
    application = report.get("application") if isinstance(report.get("application"), dict) else {}
    probe.update(
        health=health,
        presets=publication_presets(publication),
        mode=application.get("mode"),
    )
    unavailable = step_unavailable(publication, "perception")
    if publication.get("report") is None:
        return {**probe, "status": unavailable[0], "error": unavailable[1]}
    # The host computes the result age on its own clock.
    latest = _probe_latest_perception(
        probe,
        publication,
        age_ms=publication.get("result_age_ms"),
    )
    if unavailable is not None:
        # A stale or raised frame keeps its fields beside the reason it is not live.
        return {**latest, "status": unavailable[0], "error": unavailable[1]}
    return latest


def _probe_latest_perception(
    probe: dict[str, Any],
    publication: dict[str, Any],
    *,
    age_ms: Any,
) -> dict[str, Any]:
    """The latest frame's perception, as every provider's live probe reports it."""

    report = publication.get("report") if isinstance(publication.get("report"), dict) else {}
    cycle = report.get("cycle") if isinstance(report.get("cycle"), dict) else {}
    frame = {
        "frame_id": report.get("frame_id"),
        "frame_index": report.get("frame_index"),
        "captured_at_ms": report.get("timestamp_ms"),
        "perception_completed_at_ms": report.get("published_at_ms"),
        "perception_duration_ms": perception_duration_ms(publication),
        "age_ms": age_ms,
    }
    perception = cycle.get("perception")
    signals = perception.get("signals") if isinstance(perception, dict) else None
    things = perception.get("things") if isinstance(perception, dict) else None
    return {
        **probe,
        **frame,
        "status": "live",
        "signal_count": len(signals) if isinstance(signals, list) else None,
        "thing_count": len(things) if isinstance(things, list) else None,
        "perception": perception if isinstance(perception, dict) else None,
    }
