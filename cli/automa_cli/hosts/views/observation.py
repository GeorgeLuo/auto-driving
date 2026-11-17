"""Publish each new host observation capture to a local perception view."""
from __future__ import annotations

from dataclasses import dataclass, field

from autonomy.runtime.frame_loop import FramePublication

from ...frontend.perception_view import PerceptionView
from ..publications.frame_publication import publication_capture


@dataclass
class ObservationPublisher:
    """Publishes a capture once. After ``invalidate`` the same capture publishes again."""

    view: PerceptionView
    _last_capture: tuple[object, ...] | None = field(default=None, init=False)

    def invalidate(self, reason: str | None = None) -> None:
        self._last_capture = None
        self.view.invalidate_latest(reason)

    def publish(
        self,
        publication: FramePublication,
        jpeg: bytes | None,
        *,
        host_telemetry: object = None,
    ) -> bool:
        """False when the view already shows this capture at this health and status."""
        report = publication.report
        shown = (
            publication_capture(publication),
            publication.health,
            None if report is None else report.status,
            host_telemetry,
        )
        if shown == self._last_capture:
            return False
        self.view.publish(
            frame_bytes=jpeg, publication=publication, host_telemetry=host_telemetry,
        )
        self._last_capture = shown
        return True
