"""The ``hosts viewer`` command."""

from __future__ import annotations

import time
from collections.abc import Callable

from ...releases.errors import RuntimeHostError
from ...shared.open_view import launch_browser
from ..discovery import local_hosts, runtime_base_url
from ..publications.decision_publication import health_error
from .models import ViewerEvent, ViewerResult
from .polling import PERCEPTION_COPY_INTERVAL_S, pause_s
from .view_feed import ViewFeed


def serve_host_viewer(
    *, host_name: str | None = None, host_url: str | None = None, timeout_s: float = 3.0,
    emit: Callable[[ViewerEvent], None] | None = None,
) -> ViewerResult:
    """Serve the runtime view fed from a local host until interrupted.

    The host is ``host_name``, or the one at ``host_url``, the host URL
    ``hosts status`` shows. The view opens only once the host answers, and
    refreshes at the cadence of a run's view. While the host is not live its
    pages say why.
    """

    if host_name is not None:
        try:
            base_url = runtime_base_url(host_name)
        except RuntimeHostError as exc:
            return ViewerResult(2, "error", error=str(exc))
    else:
        hosts = [host for host in local_hosts() if host["url"]]
        matched = [host for host in hosts if host["url"].rstrip("/") == host_url.rstrip("/")]
        if not matched:
            return ViewerResult(2, "unknown_host", base_url=host_url, hosts=hosts)
        host_name, base_url = matched[0]["host_name"], matched[0]["url"]
    view = ViewFeed(host_name, base_url=base_url, timeout_s=timeout_s)
    try:
        publication, error = view.read_observation()
        if view.url is None or error is not None:
            return ViewerResult(2, "unavailable", host_name=host_name, base_url=base_url, error=view.error)
        view.publish(publication)
        event = ViewerEvent(host_name, base_url, health_error(publication), launch_browser(view.url))
        if emit is not None:
            emit(event)
        while True:
            started = time.monotonic()
            view.publish(view.read_observation()[0])
            time.sleep(pause_s(started, PERCEPTION_COPY_INTERVAL_S))
    except KeyboardInterrupt:
        return ViewerResult(130, "interrupted")
    finally:
        view.stop()
