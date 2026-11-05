"""Publish a host observation to its local view and latest-file exports."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...frontend.perception_view import PerceptionView
from ...shared.io.json_files import write_json_atomically
from ..publications.decision_publication import perception_text_from_publication, publication_capture
from ..publications.host_publications import OBSERVATION_IMAGE_CONTENT_TYPE


@dataclass
class ObservationPublisher:
    view: PerceptionView
    frame_path: Path
    record_dir: Path | None = None
    identity: dict[str, Any] = field(default_factory=dict)
    _last_capture: tuple[Any, ...] | None = field(default=None, init=False)

    def invalidate(self, reason: str | None = None) -> None:
        self._last_capture = None
        self.view.invalidate_latest(reason)

    def publish(self, publication: dict[str, Any], jpeg: bytes | None) -> bool:
        """Publish a changed capture; callers own freshness and error reporting."""
        if jpeg is None:
            self.invalidate()
            return False
        capture = publication_capture(publication)
        if capture == self._last_capture or not capture[0]:
            return False
        record = {**publication, **self.identity} if self.identity else publication
        self.frame_path.write_bytes(jpeg)
        self.view.publish_matched(
            frame_bytes=jpeg, content_type=OBSERVATION_IMAGE_CONTENT_TYPE, frame_record=record,
        )
        if self.record_dir is not None:
            write_json_atomically(self.record_dir / "latest_perception.json", record)
            (self.record_dir / "latest_perception.txt").write_text(
                perception_text_from_publication(publication) + "\n", encoding="utf-8",
            )
        self._last_capture = capture
        return True
