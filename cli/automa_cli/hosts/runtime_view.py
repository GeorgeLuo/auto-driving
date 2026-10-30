"""Automa's binding of the runtime viewer to its hosts and workbench replay runner."""

from __future__ import annotations

import time
from typing import Any

from ..frontend.server import RuntimeViewServer, VIEWER_PORT
from ..shared.open_view import open_view_message
from ..workbench.runner import ImageReplayRunner
from .host_status import host_state_text, host_statuses


def runtime_view_server(**options: Any) -> RuntimeViewServer:
    """Build the viewer with automa's host list and on-demand workbench runner."""

    options.setdefault("replay_runner_factory", ImageReplayRunner)
    return RuntimeViewServer(hosts=_host_list, **options)


def serve_viewer() -> int:
    """Serve the runtime pages with no runtime host until interrupted."""

    server = runtime_view_server(
        host_name="viewer",
        automation_dir=None,
        port=VIEWER_PORT,
        connected=False,
    ).start()
    print(open_view_message(server.url or ""), flush=True)
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        return 130
    finally:
        server.stop()


def _host_list() -> list[dict[str, Any]]:
    return [
        {"host_name": host["host_name"], "state": host_state_text(host)}
        for host in host_statuses()
    ]
