"""Live perception: the host's latest observation publication."""

from __future__ import annotations

from typing import Any, Callable

from autonomy.runtime.frame_loop import LATEST_JSON_PATH

from ...hosts.publications.decision_publication import health_error, publication_to_frame_record
from ...hosts.publications.host_publications import fetch_observation_publication
from ...hosts.discovery import runtime_base_url
from ...releases.errors import RuntimeHostError
from ...shared.timestamps import timestamp_ms
from .models import StreamEvent, StreamResult
from .polling import poll_events, preflight_failure, unavailable_probe
from .session import StreamView, with_view

PERCEPTION_LIVE_SCHEMA = "host_perception_live"
# Observation publication health -> probe status; "healthy" is the only live one.
_PERCEPTION_STATUS = {
    "warming": "absent",
    "absent": "absent",
    "stale": "stale",
    "unavailable": "unavailable",
    "error": "error",
}


def read_publication(base_url: str, *, timeout_s: float) -> tuple[dict[str, Any] | None, str | None]:
    try:
        return fetch_observation_publication(base_url, timeout_s=timeout_s), None
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

    def probe() -> tuple[dict[str, Any], Any]:
        publication, fetch_error = (view.read_observation() if view is not None
                                    else read_publication(base_url, timeout_s=timeout_s))
        live = _probe_perception(
            host_name=host_name, base_url=base_url, publication=publication, fetch_error=fetch_error,
        )
        return live, publication

    def outbound(event: StreamEvent) -> None:
        if view is not None:
            view.publish(event.source)
        emit(with_view(view, event, host_name=host_name, base_url=base_url))

    try:
        return poll_events(
            step="perception", probe=probe, refresh_s=refresh_s, once=once, emit=outbound,
        )
    finally:
        if view is not None:
            view.close()


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
    probe.update(
        health=health,
        presets=publication.get("presets"),
        mode=publication.get("mode"),
        frames_received=publication.get("frames_received"),
        frames_dropped=publication.get("frames_dropped"),
        processed_count=publication.get("processed_count"),
    )
    error = health_error(publication)
    if error is not None:
        return {**probe, "status": _PERCEPTION_STATUS.get(health, "error"), "error": error}
    # The host computes the result age on its own clock.
    return _probe_latest_perception(
        probe,
        publication_to_frame_record(publication),
        age_ms=publication.get("result_age_ms"),
    )


def _probe_latest_perception(
    probe: dict[str, Any],
    record: dict[str, Any],
    *,
    age_ms: Any,
) -> dict[str, Any]:
    """The latest frame's perception, as every provider's live probe reports it."""

    frame = {
        "frame_id": record.get("frame_id"),
        "frame_index": record.get("frame_index"),
        "captured_at_ms": record.get("captured_at_ms"),
        "perception_completed_at_ms": record.get("perception_completed_at_ms"),
        "perception_duration_ms": record.get("perception_duration_ms"),
        "age_ms": age_ms,
    }
    perception = record.get("perception")
    if not isinstance(perception, dict):
        return {
            **probe,
            **frame,
            "status": "absent",
            "error": f"Latest frame {record.get('frame_id')!r} has no perception result.",
        }
    signals = perception.get("signals")
    things = perception.get("things")
    return {
        **probe,
        **frame,
        "status": "live",
        "signal_count": len(signals) if isinstance(signals, list) else None,
        "thing_count": len(things) if isinstance(things, list) else None,
        "perception": perception,
    }
