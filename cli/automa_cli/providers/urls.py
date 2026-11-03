"""Pure URL and timeout validation shared by provider operations and presentation."""

from __future__ import annotations

import math
from urllib.parse import urlparse, urlunparse


def normalize_http_url(url: str) -> str:
    value = url.strip().rstrip("/")
    if "://" not in value:
        value = f"http://{value}"
    return value


def normalize_chase_url(url: str) -> str:
    """Normalize an operator HTTP/WS Metrics UI URL to its control WebSocket."""

    value = url.strip()
    if not value:
        raise ValueError("Chase URL must not be empty")
    if "://" not in value:
        value = f"http://{value}"
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https", "ws", "wss"}:
        raise ValueError(
            "Chase URL scheme must be http, https, ws, or wss "
            f"(got {parsed.scheme or 'none'!r})"
        )
    if not parsed.netloc:
        raise ValueError(f"Chase URL has no host: {url!r}")

    scheme = {
        "http": "ws",
        "https": "wss",
        "ws": "ws",
        "wss": "wss",
    }[parsed.scheme]
    path = parsed.path
    if parsed.scheme in {"http", "https"} or path in {"", "/"}:
        path = "/ws/control"
    return urlunparse((scheme, parsed.netloc, path, "", parsed.query, ""))


def require_positive_timeout(value: float) -> float:
    timeout = float(value)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a finite number greater than zero")
    return timeout


def chase_operator_url(ws_url: str) -> str:
    """Return the reloadable browser origin for a normalized Chase endpoint."""

    parsed = urlparse(ws_url)
    scheme = "https" if parsed.scheme == "wss" else "http"
    return urlunparse((scheme, parsed.netloc, "/", "", "", ""))
