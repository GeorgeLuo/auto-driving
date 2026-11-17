"""Live perception: the host's latest observation publication."""

from __future__ import annotations

from typing import Callable

from autonomy.decision_cycle.perception.interface import PerceptionText
from autonomy.runtime.frame_loop import FramePublication, LATEST_JSON_PATH
from autonomy.runtime.report import HostReport

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


def read_publication(base_url: str, *, timeout_s: float) -> tuple[FramePublication | None, str | None]:
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

    def probe() -> tuple[dict[str, object], PerceptionPublication]:
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


def _perception_record(report: HostReport | None) -> PerceptionText | None:
    if report is None:
        return None
    return report.cycle.perception


def perception_publication(publication: FramePublication | None) -> PerceptionPublication:
    """The fields the perception screen shows, read from the host's observation publication."""

    if publication is None:
        return PerceptionPublication()
    report = publication.report
    application = None if report is None else report.application
    perception = _perception_record(report)
    timing = None if report is None else report.values.timing
    cycle_ms = None if timing is None else timing.get("duration_ms")
    return PerceptionPublication(
        health=publication.health,
        presets=publication_presets(publication),
        mode=None if application is None else application.mode,
        steering=None if application is None else application.steering,
        throttle=None if application is None else application.throttle,
        reason=None if application is None else application.reason,
        cycle_ms=cycle_ms,
        age_ms=publication.result_age_ms,
        frame_id=None if report is None else report.frame_id,
        captured_at_ms=None if report is None else report.timestamp_ms,
        signal_count=None if perception is None else len(perception.signals),
        thing_count=None if perception is None else len(perception.things),
        text=perception.text.strip() if perception is not None else perception_text_from_publication(publication),
    )


def _probe_perception(
    *,
    host_name: str,
    base_url: str,
    publication: FramePublication | None,
    fetch_error: str | None,
) -> dict[str, object]:
    probe: dict[str, object] = {
        "schema": PERCEPTION_LIVE_SCHEMA,
        "host_name": host_name,
        "endpoint": f"{base_url}{LATEST_JSON_PATH}",
        "probed_at_ms": timestamp_ms(),
    }
    if fetch_error is not None or publication is None:
        return {**probe, "status": "error", "error": fetch_error or "No publication was read."}

    report = publication.report
    application = None if report is None else report.application
    probe.update(
        health=publication.health or "unknown",
        presets=publication_presets(publication),
        mode=None if application is None else application.mode,
    )
    unavailable = step_unavailable(publication, "perception")
    if report is None:
        return {**probe, "status": unavailable[0], "error": unavailable[1]}
    # The host computes the result age on its own clock.
    latest = _probe_latest_perception(probe, publication)
    if unavailable is not None:
        # A stale or raised frame keeps its fields beside the reason it is not live.
        return {**latest, "status": unavailable[0], "error": unavailable[1]}
    return latest


def _probe_latest_perception(
    probe: dict[str, object],
    publication: FramePublication,
) -> dict[str, object]:
    """The latest frame's perception, as every provider's live probe reports it."""

    report = publication.report
    perception = _perception_record(report)
    frame = {
        "frame_id": None if report is None else report.frame_id,
        "frame_index": None if report is None else report.frame_index,
        "captured_at_ms": None if report is None else report.timestamp_ms,
        "perception_completed_at_ms": None if report is None else report.published_at_ms,
        "perception_duration_ms": perception_duration_ms(publication),
        "age_ms": publication.result_age_ms,
    }
    return {
        **probe,
        **frame,
        "status": "live",
        "signal_count": None if perception is None else len(perception.signals),
        "thing_count": None if perception is None else len(perception.things),
        "perception": None if perception is None else perception.to_dict(),
    }
