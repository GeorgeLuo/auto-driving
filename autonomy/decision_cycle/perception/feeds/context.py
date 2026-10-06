"""Request context that resolves shared plugin feeds.

A ``PerceptionRequest`` carries one sensor frame. Each feed is resolved
once per request and shared by every plugin that declares it; a provider that
raises ``PerceptionFeedUnavailable`` or returns nothing records an error
for that feed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TypeVar

from autonomy.decision_cycle.perception.feeds.interface import PerceptionFeedUnavailable
from autonomy.shared_memory import SharedMemory
from autonomy.vehicle import SensorFrame, SensorReading


FeedT = TypeVar("FeedT")


@dataclass
class PerceptionRequest:
    """Framework request used to resolve shared feeds for plugins."""

    sensor_frame: SensorFrame
    output_dir: Path | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    _feeds: dict[str, Any] = field(default_factory=dict, repr=False)
    _feed_errors: dict[str, str] = field(default_factory=dict, repr=False)
    shared_memory: SharedMemory | None = field(default=None, repr=False, compare=False)

    def sensor(self, sensor_id: str) -> SensorReading | None:
        return self.sensor_frame.readings.get(sensor_id)

    def resolve_feed(
        self,
        feed_id: str,
        provider: Callable[[], FeedT],
    ) -> FeedT | None:
        """Resolve one derived input once and share it across interested plugins."""

        if feed_id in self._feeds:
            return self._feeds[feed_id]
        if feed_id in self._feed_errors:
            return None
        try:
            feed = provider()
        except PerceptionFeedUnavailable as exc:
            self._feed_errors[feed_id] = str(exc)
            return None
        if feed is None:
            self._feed_errors[feed_id] = "feed provider returned no value"
            return None
        self._feeds[feed_id] = feed
        return feed

    def feed(self, feed_id: str) -> Any | None:
        return self._feeds.get(feed_id)

    def feed_error(self, feed_id: str) -> str | None:
        return self._feed_errors.get(feed_id)

    def feed_summary(self) -> dict[str, Any]:
        return {
            "available": {
                feed_id: type(feed).__name__
                for feed_id, feed in sorted(self._feeds.items())
            },
            "errors": dict(sorted(self._feed_errors.items())),
        }
