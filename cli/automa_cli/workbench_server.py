"""Loopback HTTP boundary for the perception-memory workbench."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qs, urlparse

from .loopback_http import (
    LoopbackHTTPRequestHandler,
    LoopbackHTTPServer,
    start_server_thread,
    stop_server_thread,
    validate_loopback_host,
)
from .workbench_contract import (
    ReplayActionError,
    WORKBENCH_ACTION_RESULT_SCHEMA,
    WORKBENCH_ERROR_SCHEMA,
    WORKBENCH_HOST,
    WORKBENCH_MAX_ACTION_BYTES,
    WORKBENCH_SEQUENCE_ID,
    WORKBENCH_SERVER_SCHEMA,
)
from .workbench_source import SourceValidationError


WORKBENCH_HTML_PATH = Path(__file__).with_name("workbench.html")


class ReplayRunner(Protocol):
    @property
    def server_identity(self) -> str:
        ...

    def state(self) -> dict[str, Any]:
        ...

    def close(self) -> None:
        ...

    def dispatch(self, action: str, **kwargs: Any) -> dict[str, Any]:
        ...

    def frame_detail(
        self,
        frame_id: str,
        *,
        run_id: str,
    ) -> dict[str, Any] | None:
        ...

    def frame_bytes(
        self,
        frame_id: str | None = None,
        *,
        run_id: str,
    ) -> tuple[bytes, str] | None:
        ...


class WorkbenchServer:
    """Loopback-only HTTP boundary for one persistent replay runner."""

    def __init__(
        self,
        runner: ReplayRunner,
        *,
        host: str = WORKBENCH_HOST,
        port: int = 0,
    ) -> None:
        validate_loopback_host(host, owner="workbench server")
        self.runner = runner
        self.host = host
        self.preferred_port = int(port)
        self._httpd: _WorkbenchHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._started_at_ms: int | None = None

    @property
    def url(self) -> str | None:
        return self._httpd.loopback_url if self._httpd is not None else None

    def start(self) -> "WorkbenchServer":
        if self._httpd is not None:
            return self
        httpd = _WorkbenchHTTPServer.bind_with_ephemeral_fallback(
            host=self.host,
            preferred_port=self.preferred_port,
            handler=_WorkbenchHTTPHandler,
        )
        httpd.workbench = self
        self._httpd = httpd
        self._started_at_ms = _now_ms()
        self._thread = start_server_thread(
            httpd,
            name=f"automa-workbench-http-{self.runner.server_identity[-8:]}",
        )
        return self

    def stop(self) -> None:
        if self._httpd is None:
            return
        try:
            stop_server_thread(self._httpd, self._thread)
        finally:
            self._httpd = None
            self._thread = None
            self.runner.close()

    def health_payload(self) -> dict[str, Any]:
        state = self.runner.state()
        return {
            "schema": WORKBENCH_SERVER_SCHEMA,
            "server_identity": self.runner.server_identity,
            "available": self._httpd is not None,
            "host": self.host,
            "port": self._httpd.server_address[1] if self._httpd else None,
            "url": self.url,
            "started_at_ms": self._started_at_ms,
            "sequence_id": WORKBENCH_SEQUENCE_ID,
            "run_id": state.get("run_id"),
            "phase": state.get("phase"),
            "persistent_across_terminal_state": True,
            "observation_only": True,
        }

    def state_payload(self) -> dict[str, Any]:
        return self.runner.state()

    def action_payload(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ReplayActionError(
                "action body must be a JSON object",
                status_code=400,
                boundary="input",
            )
        allowed = {
            "action",
            "run_id",
            "source_dir",
            "cadence_ms",
            "plugin_dir",
            "active_plugin_ids",
        }
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise ReplayActionError(
                f"unknown action fields: {', '.join(str(item) for item in unknown)}",
                status_code=400,
                boundary="input",
            )
        action = payload.get("action")
        if not isinstance(action, str) or not action.strip():
            raise ReplayActionError(
                "action must be a non-empty string",
                status_code=400,
                boundary="input",
            )
        run_id = payload.get("run_id")
        if run_id is not None and (not isinstance(run_id, str) or not run_id.strip()):
            raise ReplayActionError(
                "run_id must be a non-empty string",
                status_code=400,
                boundary="input",
            )
        source_dir = payload.get("source_dir")
        if source_dir is not None and not isinstance(source_dir, str):
            raise ReplayActionError(
                "source_dir must be a path string",
                status_code=400,
                boundary="input",
            )
        cadence_ms = payload.get("cadence_ms")
        if cadence_ms is not None and (
            isinstance(cadence_ms, bool) or not isinstance(cadence_ms, int)
        ):
            raise ReplayActionError(
                "cadence_ms must be an integer",
                status_code=400,
                boundary="input",
            )
        plugin_dir = payload.get("plugin_dir")
        if plugin_dir is not None and not isinstance(plugin_dir, str):
            raise ReplayActionError(
                "plugin_dir must be a path string",
                status_code=400,
                boundary="input",
            )
        active_plugin_ids = payload.get("active_plugin_ids")
        if active_plugin_ids is not None:
            if not isinstance(active_plugin_ids, list) or any(
                not isinstance(item, str) for item in active_plugin_ids
            ):
                raise ReplayActionError(
                    "active_plugin_ids must be an array of strings",
                    status_code=400,
                    boundary="input",
                )
        try:
            state = self.runner.dispatch(
                action,
                run_id=run_id,
                source_dir=source_dir,
                cadence_ms=cadence_ms,
                plugin_dir=plugin_dir,
                active_plugin_ids=active_plugin_ids,
            )
        except SourceValidationError as exc:
            raise ReplayActionError(
                str(exc),
                status_code=422,
                boundary="source",
                state=self.state_payload(),
            ) from exc
        return {
            "schema": WORKBENCH_ACTION_RESULT_SCHEMA,
            "ok": True,
            "action": action,
            "state": state,
        }


class _WorkbenchHTTPServer(LoopbackHTTPServer):
    workbench: WorkbenchServer


class _WorkbenchHTTPHandler(LoopbackHTTPRequestHandler):
    server: _WorkbenchHTTPServer
    content_security_policy = (
        "default-src 'self'; connect-src 'self'; img-src 'self' data:; "
        "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'"
    )

    def do_GET(self) -> None:
        self._handle_get(include_body=True)

    def do_HEAD(self) -> None:
        self._handle_get(include_body=False)

    def _handle_get(self, *, include_body: bool) -> None:
        request = urlparse(self.path)
        if request.path in {"/", "/index.html"}:
            self._serve_html(include_body=include_body)
            return
        if request.path == "/favicon.ico":
            self._send(204, b"", "image/x-icon", include_body=False)
            return
        if request.path in {"/api/health", "/api/state"}:
            payload = (
                self.server.workbench.health_payload()
                if request.path == "/api/health"
                else self.server.workbench.state_payload()
            )
            self._send_json(200, payload, include_body=include_body)
            return
        if request.path in {"/api/frame", "/api/frame-detail"}:
            self._serve_frame(request.path, request.query, include_body=include_body)
            return
        self._send_json(
            404,
            _error_payload("route", f"unknown route: {request.path}", None),
            include_body=include_body,
        )

    def _serve_frame(
        self, route: str, query_string: str, *, include_body: bool
    ) -> None:
        query = parse_qs(query_string, keep_blank_values=True)
        frame_id = _query_one(query, "frame_id")
        run_id = _query_one(query, "run_id")
        if not run_id or not frame_id:
            self._send_json(
                400,
                _error_payload(
                    "input",
                    "run_id and frame_id are required",
                    self.server.workbench.state_payload(),
                ),
                include_body=include_body,
            )
            return
        try:
            if route == "/api/frame-detail":
                detail = self.server.workbench.runner.frame_detail(
                    frame_id,
                    run_id=run_id,
                )
            else:
                frame = self.server.workbench.runner.frame_bytes(
                    frame_id,
                    run_id=run_id,
                )
        except ReplayActionError as exc:
            self._send_json(
                exc.status_code,
                _error_payload(
                    exc.boundary,
                    str(exc),
                    exc.state or self.server.workbench.state_payload(),
                ),
                include_body=include_body,
            )
            return
        if route == "/api/frame-detail":
            if detail is None:
                self._send_json(
                    404,
                    _error_payload(
                        "frame",
                        "processed frame detail is unavailable",
                        self.server.workbench.state_payload(),
                    ),
                    include_body=include_body,
                )
                return
            self._send_json(200, detail, include_body=include_body)
            return
        if frame is None:
            self._send_json(
                404,
                _error_payload(
                    "frame",
                    "frame bytes are unavailable",
                    self.server.workbench.state_payload(),
                ),
                include_body=include_body,
            )
            return
        body, content_type = frame
        self._send(200, body, content_type, include_body=include_body)

    def do_POST(self) -> None:
        request = urlparse(self.path)
        if request.path != "/api/action":
            self._send_json(
                404,
                _error_payload("route", f"unknown route: {request.path}", None),
            )
            return
        content_length = self.headers.get("Content-Length")
        try:
            size = int(content_length) if content_length is not None else -1
        except ValueError:
            size = -1
        if size < 0 or size > WORKBENCH_MAX_ACTION_BYTES:
            self._send_json(
                413,
                _error_payload(
                    "input",
                    f"request body must be between 0 and {WORKBENCH_MAX_ACTION_BYTES} bytes",
                    None,
                ),
            )
            return
        try:
            payload = json.loads(self.rfile.read(size).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._send_json(400, _error_payload("input", f"invalid JSON: {exc}", None))
            return
        try:
            result = self.server.workbench.action_payload(payload)
        except ReplayActionError as exc:
            self._send_json(
                exc.status_code,
                _error_payload(
                    exc.boundary,
                    str(exc),
                    exc.state or self.server.workbench.state_payload(),
                ),
            )
            return
        self._send_json(200, result)

    def _serve_html(self, *, include_body: bool) -> None:
        try:
            body = WORKBENCH_HTML_PATH.read_bytes()
        except OSError as exc:
            self._send_json(
                500,
                _error_payload("server", str(exc), None),
                include_body=include_body,
            )
            return
        self._send(
            200,
            body,
            "text/html; charset=utf-8",
            include_body=include_body,
        )


def _query_one(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key)
    return values[0] if values else None


def _error_payload(
    boundary: str,
    message: str,
    state: dict[str, Any] | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": WORKBENCH_ERROR_SCHEMA,
        "ok": False,
        "boundary": boundary,
        "message": message,
        "recovery": "Inspect the returned state and use the allowed action.",
    }
    if state is not None:
        payload["state"] = state
    return payload


def _now_ms() -> int:
    return int(time.time() * 1000)


__all__ = ["WorkbenchServer"]
