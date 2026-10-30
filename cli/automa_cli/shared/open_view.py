"""Opening a runtime view's page in a browser tab."""

from __future__ import annotations

import webbrowser

from .browser_result import BrowserOpenResult


def launch_browser(url: str) -> BrowserOpenResult:
    """Open ``url`` in a browser tab; return whether the launcher accepted the request."""

    try:
        opened = webbrowser.open(url, new=2)
    except (OSError, webbrowser.Error) as exc:
        opened = False
        detail = f"{type(exc).__name__}: {exc}"
    else:
        detail = "browser launcher returned false"
    return BrowserOpenResult(url, opened, None if opened else detail)
