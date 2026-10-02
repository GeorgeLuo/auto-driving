"""Feed declarations and the provider contract.

A plugin declares each input as a ``PerceptionPluginInput``: the name it reads,
the shared feed ID, and the provider spec. A provider receives the
perception request and that declaration. It raises
``PerceptionFeedUnavailable`` when the feed cannot be derived from
the sensor frame.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from autonomy.decision_cycle.perception.feeds.context import PerceptionRequest


class PerceptionFeedUnavailable(RuntimeError):
    """A declared plugin input cannot be derived from the sensor frame."""


@dataclass(frozen=True)
class PerceptionPluginInput:
    """One named feed injected into a plugin by the framework."""

    name: str
    feed_id: str
    provider_spec: str

    def __post_init__(self) -> None:
        if not self.name or not self.feed_id or not self.provider_spec:
            raise ValueError("plugin inputs require name, feed_id, and provider_spec")
        if ":" not in self.provider_spec:
            raise ValueError("feed provider spec must be 'module.path:callable'")

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


FeedProvider = Callable[["PerceptionRequest", PerceptionPluginInput], Any]
