"""The plugin catalog route the runtime viewer and its workbench serve."""

from __future__ import annotations

import json
from typing import Any, Callable
from urllib.parse import urlsplit

from autonomy.runtime.plugin_catalog import catalog_write_error

from .loopback_http import LOOPBACK_HOSTS


def serve_plugin_catalog(handler: Any, api: Callable | None, *, include_body: bool = True) -> None:
    """Thin HTTP adapter shared by the runtime viewer and replay workbench."""

    if handler.command == "POST":
        authority = handler.headers.get("Host", "")
        try:
            address = urlsplit(f"http://{authority}")
            local = (address.hostname in LOOPBACK_HOSTS
                     and address.port == handler.server.server_port
                     and address.username is None and address.password is None
                     and not address.path and not address.query and not address.fragment)
        except ValueError:
            local = False
        error = (catalog_write_error(handler.headers, origin=f"http://{authority}") if local else
                 (403, {"ok": False, "status": "failed", "error": "catalog writes require a loopback host"}))
        if error is not None:
            handler._send_json(*error, include_body=include_body)
            return
    if api is None:
        handler._send_json(503, {"ok": False, "error": "catalog unavailable"}, include_body=include_body)
        return
    try:
        payload = None
        if handler.command == "POST":
            size = int(handler.headers.get("Content-Length", "0"))
            if size < 0:
                raise ValueError("negative content length")
            payload = json.loads(handler.rfile.read(size))
            if not isinstance(payload, dict):
                raise ValueError("upload body must be an object")
        result = api(payload)
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        result = {"ok": False, "status": "failed", "error": str(exc)}
    handler._send_json(200 if result.get("ok") else 400, result, include_body=include_body)
