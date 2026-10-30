"""The ``hosts viewer`` command."""

from __future__ import annotations

import time

from ...shared.command_result import CommandResult
from ...releases.errors import RuntimeHostError
from ...shared.open_view import open_view_message
from ..publications.decision_publication import health_error
from ..discovery import local_hosts, runtime_base_url
from .view_feed import ViewFeed
from .polling import PERCEPTION_COPY_INTERVAL_S, pause_s


def serve_host_viewer(
    *, host_name: str | None = None, host_url: str | None = None, timeout_s: float = 3.0,
) -> CommandResult:
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
            return CommandResult(2, str(exc))
    else:
        hosts = [host for host in local_hosts() if host["url"]]
        matched = [host for host in hosts if host["url"].rstrip("/") == host_url.rstrip("/")]
        if not matched:
            names = ", ".join(f"{host['host_name']} at {host['url']}" for host in hosts)
            return CommandResult(2, f"No host at {host_url}. " + (f"Hosts: {names}." if names else "None is running."))
        host_name, base_url = matched[0]["host_name"], matched[0]["url"]
    view = ViewFeed(host_name, base_url=base_url, timeout_s=timeout_s)
    try:
        publication, error = view.read_observation()
        if view.url is None or error is not None:
            return CommandResult(2, f"Cannot view host {host_name} at {base_url}: {view.error}")
        view.publish(publication)
        print(f"Viewing host {host_name} at {base_url}", flush=True)
        not_live = health_error(publication)
        if not_live is not None:
            print(f"Not live: {not_live}", flush=True)
        print(open_view_message(view.url), flush=True)
        while True:
            started = time.monotonic()
            view.publish(view.read_observation()[0])
            time.sleep(pause_s(started, PERCEPTION_COPY_INTERVAL_S))
    except KeyboardInterrupt:
        return CommandResult(130, "")
    finally:
        view.stop()
