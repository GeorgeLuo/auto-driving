"""The plugin catalog route the runtime viewer and its workbench serve."""

from __future__ import annotations

from typing import Any, Callable

from .loopback_http import local_write_error, read_json_object


def serve_plugin_catalog(handler: Any, api: Callable | None, *, include_body: bool = True) -> None:
    """Thin HTTP adapter shared by the runtime viewer and replay workbench."""

    if handler.command == "POST":
        error = local_write_error(handler)
        if error is not None:
            handler._send_json(*error, include_body=include_body)
            return
    if api is None:
        handler._send_json(503, {"ok": False, "error": "catalog unavailable"}, include_body=include_body)
        return
    try:
        payload = None
        if handler.command == "POST":
            payload = read_json_object(handler, what="upload body")
        result = api(payload)
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        result = {"ok": False, "status": "failed", "error": str(exc)}
    handler._send_json(200 if result.get("ok") else 400, result, include_body=include_body)
