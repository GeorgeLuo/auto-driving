"""Opening a runtime view's page in a browser tab."""

from __future__ import annotations

import webbrowser


def open_view_message(url: str) -> str:
    """Open ``url`` in a browser tab; return the line telling the user where it is."""

    try:
        opened = webbrowser.open(url, new=2)
    except (OSError, webbrowser.Error) as exc:
        opened = False
        detail = f"{type(exc).__name__}: {exc}"
    else:
        detail = "browser launcher returned false"
    if opened:
        return f"Browser opened: {url}"
    return (
        f"Warning: could not open the browser ({detail}). "
        f"Open manually: {url}"
    )
