"""Read a completed host report and its separate telemetry for the runtime pages."""

from __future__ import annotations

import time
from typing import Any

from ..publications.decision import (
    accept_decision_publication,
    decision_view_frame,
)
from ..publications.host_publications import (
    TransactionCache,
    fetch_host_telemetry_latest,
    fetch_frame_sample,
)
from ..publications.host_telemetry import (
    HostTelemetryError,
    host_telemetry_failure,
    join_host_telemetry_to_decision,
    normalize_host_telemetry_record,
)
from ...frontend.server import RuntimeViewServer
from .observation import ObservationPublisher
from ..publications.frame_publication import publication_capture


def _accepted_pair(
    base_url: str,
    *,
    host_name: str,
    timeout_s: float,
    cache: TransactionCache | None = None,
) -> tuple[dict[str, Any], tuple[bytes, str] | None]:
    """Accept the frame the host published. The image is optional."""

    publication, jpeg = fetch_frame_sample(base_url, timeout_s=timeout_s, cache=cache)
    normalized = accept_decision_publication(
        publication,
        host_name=host_name,
        now_ms=int(time.time() * 1000),
    )
    image = (jpeg, "image/jpeg") if jpeg else None
    return normalized, image


class DecisionViewAdapter:
    """Publish one accepted frame for perception, memory, and decision."""

    def __init__(
        self,
        *,
        host_name: str,
        base_url: str,
        view_server: RuntimeViewServer,
        timeout_s: float,
    ) -> None:
        self.host_name = host_name
        self.base_url = base_url
        self.view_server = view_server
        self.publisher = ObservationPublisher(view_server.perception)
        self.timeout_s = timeout_s
        self._transaction_cache = TransactionCache()
        self.publication: dict[str, Any] | None = None

    def publish_frame(
        self,
        normalized: dict[str, Any],
        image: tuple[bytes, str] | None,
    ) -> bool:
        decision_view_frame(normalized)
        publication = normalized["publication"]
        if self.publication is None or publication_capture(publication) != publication_capture(self.publication):
            self.publisher.publish(publication, image[0] if image else None)
        telemetry = read_host_telemetry_panel(
            self.base_url, normalized_decision=normalized,
            host_name=self.host_name, timeout_s=self.timeout_s,
        )
        self.publication = {**normalized["publication"], "host_telemetry": telemetry}
        self.publisher.publish(self.publication, image[0] if image else None)
        return True

    def publish_publication(self, publication: dict[str, Any], jpeg: bytes | None) -> bool:
        normalized = accept_decision_publication(
            publication, host_name=self.host_name, now_ms=int(time.time() * 1000),
        )
        return self.publish_frame(normalized, (jpeg, "image/jpeg") if jpeg else None)

    def refresh(self) -> bool:
        normalized, image = _accepted_pair(
            self.base_url,
            host_name=self.host_name,
            timeout_s=self.timeout_s, cache=self._transaction_cache,
        )
        return self.publish_frame(normalized, image)


def read_host_telemetry_panel(
    base_url: str,
    *,
    normalized_decision: dict[str, Any],
    host_name: str,
    timeout_s: float = 3.0,
    now_ms: int | None = None,
) -> dict[str, Any]:
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
