"""Publish each new host observation capture to a local perception view."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ...frontend.perception_view import PerceptionView
from ..publications.decision_publication import publication_capture


@dataclass
class ObservationPublisher:
    """Publishes a capture once. After ``invalidate`` the same capture publishes again."""

    view: PerceptionView
    _last_capture: tuple[Any, ...] | None = field(default=None, init=False)

    def invalidate(self, reason: str | None = None) -> None:
        self._last_capture = None
        self.view.invalidate_latest(reason)

    def publish(self, record: dict[str, Any], jpeg: bytes) -> bool:
        """False when the view already shows this capture."""
        capture = publication_capture(record)
        if capture == self._last_capture:
            return False
        self.view.publish(frame_bytes=jpeg, frame_record=record)
        self._last_capture = capture
        return True
