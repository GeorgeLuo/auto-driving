"""The local viewer a continuous text stream serves.

Polling does not import this module. A step operation opens a session when
the command asks to serve a view, publishes through it, and closes it on
completion, interruption, and failure. The renderer receives only
``ViewState``.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from ...hosts.views.view_feed import ViewFeed
from .models import StreamEvent, ViewState


class StreamView:
    """One local runtime view for the duration of a stream."""

    def __init__(self, host_name: str, *, base_url: str, timeout_s: float) -> None:
        self._feed = ViewFeed(host_name, base_url=base_url, timeout_s=timeout_s)

    def read_observation(self) -> tuple[dict[str, Any] | None, str | None]:
        return self._feed.read_observation()

    def publish(self, publication: dict[str, Any] | None) -> None:
        self._feed.publish(publication)

    def view_state(self) -> ViewState:
        url, error, page, decision_error = self._feed.state()
        return ViewState(url=url, error=error, decision_page=page, decision_error=decision_error)

    def close(self) -> None:
        self._feed.stop()


def with_view(view: StreamView | None, event: StreamEvent, **fields: Any) -> StreamEvent:
    """``event`` plus the view's addresses when a session is open."""

    return replace(event, view=None if view is None else view.view_state(), **fields)
