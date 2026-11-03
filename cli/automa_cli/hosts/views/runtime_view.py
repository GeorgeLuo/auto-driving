"""Automa's binding of the runtime viewer to its hosts and workbench replay runner."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from ...frontend.server import VIEWER_PORT, RuntimeViewServer
from ...shared.browser_result import BrowserOpenResult
from ...shared.open_view import launch_browser
from ...workbench.runner import ImageReplayRunner
from ..host_formatting import host_state_text
from ..host_status import host_statuses


def runtime_view_server(**options: Any) -> RuntimeViewServer:
    """Build the viewer with automa's host list and on-demand workbench runner."""

    options.setdefault("replay_runner_factory", ImageReplayRunner)
    return RuntimeViewServer(hosts=_host_list, **options)


def serve_viewer(*, emit: Callable[[BrowserOpenResult], None] | None = None) -> int:
    """Serve the runtime pages with no runtime host until interrupted."""

    server = runtime_view_server(
        host_name="viewer",
        automation_dir=None,
        port=VIEWER_PORT,
        connected=False,
    ).start()
    browser = launch_browser(server.url or "")
    if emit is not None:
        emit(browser)
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
