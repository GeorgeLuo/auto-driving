"""Read a completed host report and its separate telemetry for the runtime pages."""

from __future__ import annotations

import time

from autonomy.runtime.frame_loop import FramePublication

from ..publications.decision import accept_decision_publication
from ..publications.host_publications import fetch_host_telemetry_latest
from ..publications.host_telemetry import (
    HostTelemetryError,
    host_telemetry_failure,
    join_host_telemetry_to_decision,
    normalize_host_telemetry_record,
)
from .observation import ObservationPublisher
from ..publications.frame_publication import publication_capture


class DecisionViewAdapter:
    """Publish one accepted frame for perception, memory, and decision."""

    def __init__(
        self,
        *,
        host_name: str,
        base_url: str,
        publisher: ObservationPublisher,
        timeout_s: float,
    ) -> None:
        self.host_name = host_name
        self.base_url = base_url
        self.publisher = publisher
        self.timeout_s = timeout_s
        self.publication: FramePublication | None = None
        self.host_telemetry: dict[str, object] | None = None

    def publish_frame(self, normalized: dict[str, object], jpeg: bytes | None) -> None:
        publication = normalized["publication"]
        if not isinstance(publication, FramePublication):
            raise TypeError("decision publication is missing its frame")
        if self.publication is None or publication_capture(publication) != publication_capture(self.publication):
            # A new frame shows before its telemetry read answers.
            self.publisher.publish(publication, jpeg)
        telemetry = read_host_telemetry_panel(
            self.base_url, normalized_decision=normalized,
            host_name=self.host_name, timeout_s=self.timeout_s,
        )
        self.publication = publication
        self.host_telemetry = telemetry
        self.publisher.publish(publication, jpeg, host_telemetry=telemetry)

    def publish_publication(self, publication: FramePublication, jpeg: bytes | None) -> None:
        normalized = accept_decision_publication(publication, host_name=self.host_name)
        self.publish_frame(normalized, jpeg)


def read_host_telemetry_panel(
    base_url: str,
    *,
    normalized_decision: dict[str, object],
    host_name: str,
    timeout_s: float = 3.0,
    now_ms: int | None = None,
) -> dict[str, object]:
    """Read one latest host telemetry point and join it to this decision.

    A refresh performs one latest read. Identity, freshness, and transport
    results stay attached to that read.
    """

    effective_now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    try:
        raw = fetch_host_telemetry_latest(base_url, timeout_s=timeout_s)
        point = normalize_host_telemetry_record(
            raw,
            now_ms=effective_now_ms,
            host_name=host_name,
        )
        return join_host_telemetry_to_decision(
            point,
            normalized_decision,
            host_name=host_name,
        )
    except HostTelemetryError as exc:
        return host_telemetry_failure(exc.reason, message=exc.message_text)
    except (ConnectionError, OSError, TypeError, ValueError) as exc:
        return host_telemetry_failure(
            "publisher_missing",
            message=f"Host telemetry is unavailable: {type(exc).__name__}: {exc}",
        )
