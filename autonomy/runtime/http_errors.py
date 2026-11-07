"""The error reply every Automa HTTP server sends, and the guard on its writes.

Vehicle runtimes (``routes.py``) and the CLI's loopback pages answer a
refused or failed request with ``error_payload``: ``ok`` false and the
``error`` a person reads. A route adds fields only when its client reads
them. Publications that report their own health, such as the frame and
telemetry publications, are not errors and keep their schemas.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def error_payload(message: str, **context: Any) -> dict[str, Any]:
    """``{"ok": false, "error": message}``, with any ``context`` its client reads."""

    return {"ok": False, "error": message, **context}


def write_error(headers: Mapping[str, str], *, origin: str) -> tuple[int, dict[str, Any]] | None:
    """Refuse a write that a page on another origin could send, before reading its body.

    The CLI posts JSON; a page may post only to its own origin. A form or
    ``no-cors`` fetch from another site cannot set ``application/json``
    without a preflight, so either check stops it.
    """

    supplied = headers.get("Origin")
    if (supplied is not None and supplied != origin
            or headers.get("Sec-Fetch-Site") == "cross-site"):
        return 403, error_payload("writes require the same origin")
    if headers.get("Content-Type", "").partition(";")[0].strip().lower() != "application/json":
        return 415, error_payload("writes require application/json")
    return None
