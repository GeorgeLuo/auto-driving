"""The ``hosts viewer`` command."""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlencode

from ...frontend.v2_page import V2_PAGE_PATH
from ...shared.browser_result import BrowserOpenResult
from ..discovery import local_hosts, runtime_base_url
from ..errors import RuntimeHostError
from ..publications.frame_publication import health_error
from ..publications.host_publications import fetch_frame_sample
from .models import ViewerEvent, ViewerResult

# Opens the viewer's ``page``, emitting the browser's result; its exit code: 0 when it opened a
# viewer already serving, 130 when the viewer it served was interrupted.
DeskViewer = Callable[[str, Callable[[BrowserOpenResult], None]], int]


def serve_host_viewer(
    *, desk: DeskViewer, host_name: str | None = None, host_url: str | None = None, timeout_s: float = 3.0,
    emit: Callable[[ViewerEvent], None] | None = None,
) -> ViewerResult:
    """Open the v2 page on the host's stream, once the host answers.

    The host is ``host_name``, or the one at ``host_url``, the host URL
    ``hosts status`` shows. ``desk`` opens the page in the runtime viewer,
    reusing one already serving.
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
    try:
        publication, _ = fetch_frame_sample(base_url, timeout_s=timeout_s)
    except (ConnectionError, OSError, TypeError, ValueError) as exc:
        return ViewerResult(2, "unavailable", host_name=host_name, base_url=base_url,
                            error=f"{type(exc).__name__}: {exc}")
    not_live = health_error(publication)

    def opened(browser: BrowserOpenResult) -> None:
        if emit is not None:
            emit(ViewerEvent(host_name, base_url, not_live, browser))

    code = desk(f"{V2_PAGE_PATH}?{urlencode({'host': host_name})}", opened)
    return ViewerResult(code, "opened" if code == 0 else "interrupted")
