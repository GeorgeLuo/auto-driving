"""Shared runtime page hosting, independent of any one processing layer."""

from __future__ import annotations

import json
import os
import threading
import time
import zlib
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .perception_view import (
    PerceptionView,
    VIEW_SCHEMA,
    VIEW_RECORD_NAME,
    VIEW_HTML_PATH,
)
from .decision_view import (
    DecisionView,
    DecisionViewError,
    decision_error_payload,
    parse_generation_query,
    parse_image_query,
)

from .loopback_http import (
    LoopbackHTTPRequestHandler,
    LoopbackHTTPServer,
    start_server_thread,
    stop_server_thread,
    validate_loopback_host,
)


VIEW_HOST = "127.0.0.1"
RUNTIME_VIEW_HTML_PATH = Path(__file__).with_name("runtime_view.html")
MEMORY_VIEW_HTML_PATH = Path(__file__).with_name("memory_view.html")
DECISION_VIEW_HTML_PATH = Path(__file__).with_name("live_decision_view.html")


class RuntimeViewServer:
    """Serve peer runtime pages; retain the existing discovery and health contract."""

    def __init__(
        self,
        *,
        vehicle_id: str,
        automation_dir: Path,
        host: str = VIEW_HOST,
        port: int | None = None,
        run_id: str | None = None,
        worker_pid: int | None = None,
        decision_activation: dict[str, Any] | None = None,
        decision_activation_path: Path | None = None,
        decision_provider_identity: dict[str, Any] | None = None,
    ) -> None:
        validate_loopback_host(host, owner="runtime view")
        self.vehicle_id = vehicle_id
        self.automation_dir = automation_dir
        self.host = host
        self.preferred_port = _vehicle_view_port(vehicle_id) if port is None else int(port)
        self.run_id = run_id
        self.worker_pid = (
            int(worker_pid)
            if isinstance(worker_pid, int)
            else None
            if decision_provider_identity is not None
            else os.getpid()
        )
        self.record_path = automation_dir / VIEW_RECORD_NAME
        self.perception = PerceptionView(vehicle_id=vehicle_id)
        self.decision = DecisionView(
            vehicle_id=vehicle_id,
            run_id=run_id,
            worker_pid=self.worker_pid,
            activation=decision_activation,
            activation_path=(
                decision_activation_path
                if decision_activation_path is not None
                else automation_dir.parent / "decision" / "active.json"
            ),
            provider_identity=decision_provider_identity,
        )
        self._httpd: _RuntimeHttpServer | None = None
        self._thread: threading.Thread | None = None
        self._started_at_ms: int | None = None

    @property
    def url(self) -> str | None:
        if self._httpd is None:
            return None
        return self._httpd.loopback_url

    def start(self) -> "RuntimeViewServer":
        if self._httpd is not None:
            return self
        self.automation_dir.mkdir(parents=True, exist_ok=True)
        httpd = _RuntimeHttpServer.bind_with_ephemeral_fallback(
            host=self.host,
            preferred_port=self.preferred_port,
            handler=_RuntimeViewHandler,
        )
        httpd.publisher = self
        self._httpd = httpd
        self._started_at_ms = _timestamp_ms()
        self._thread = start_server_thread(
            httpd,
            name=f"automa-runtime-view-{self.vehicle_id}",
        )
        _write_json(self.record_path, self.describe())
        return self

    def describe(self, *, status: str = "running") -> dict[str, Any]:
        # Keep the established discovery record/schema for existing CLI readers.
        return {
            "schema": VIEW_SCHEMA,
            "vehicle_id": self.vehicle_id,
            "run_id": self.run_id,
            "worker_pid": self.worker_pid,
            "status": status,
            "available": status == "running" and self._httpd is not None,
            "url": self.url,
            "host": self.host,
            "port": self._httpd.server_address[1] if self._httpd is not None else None,
            "pid": os.getpid(),
            "started_at_ms": self._started_at_ms,
            "record_path": str(self.record_path),
        }

    def health_payload(self) -> dict[str, Any]:
        return {
            **self.describe(),
            **self.perception.health_payload(),
            "decision": self.decision.health_payload(),
        }

    def decision_navigation_html(self, *, compact: bool) -> str:
        """Return a usable decision link or an explicit unavailable status."""

        page_url = self.decision.page_url()
        if page_url is not None:
            if compact:
                return (
                    f'<a class="nav" href="{page_url}" '
                    'style="color:inherit;text-decoration:none;border:1px solid var(--line);'
                    'border-radius:4px;padding:6px 10px;font-weight:600;">Decision view</a>'
                )
            return (
                f'<a href="{page_url}">Decision'
                '<span>View the exact image and authority facts for the current accepted cycle.</span></a>'
            )
        if compact:
            return (
                '<span class="nav" title="Start the live decision monitor for this vehicle." '
                'style="color:#6b7280;border:1px solid var(--line);border-radius:4px;'
                'padding:6px 10px;font-weight:600;">Decision view unavailable</span>'
            )
        return (
            '<span class="decision-unavailable">Decision view unavailable'
            '<span>Start `automa vehicles decision live --id &lt;vehicle&gt;` to publish this view.</span></span>'
        )

    def stop(self) -> None:
        httpd = self._httpd
        thread = self._thread
        if httpd is None:
            return
        stop_server_thread(httpd, thread)
        self.decision.stop()
        _write_json(self.record_path, self.describe(status="stopped"))
        self._httpd = None
        self._thread = None


class _RuntimeHttpServer(LoopbackHTTPServer):
    publisher: RuntimeViewServer


class _RuntimeViewHandler(LoopbackHTTPRequestHandler):
    server: _RuntimeHttpServer

    def do_GET(self) -> None:
        self._handle_request(include_body=True)

    def do_HEAD(self) -> None:
        self._handle_request(include_body=False)

    def do_POST(self) -> None:
        self._reject_write_method()

    def do_PUT(self) -> None:
        self._reject_write_method()

    def do_PATCH(self) -> None:
        self._reject_write_method()

    def do_DELETE(self) -> None:
        self._reject_write_method()

    def do_OPTIONS(self) -> None:
        self._reject_write_method()

    def do_TRACE(self) -> None:
        self._reject_write_method()

    def do_CONNECT(self) -> None:
        self._reject_write_method()

    def _reject_write_method(self) -> None:
        self._send_json(405, {"error": "GET and HEAD only"})

    def _handle_request(self, *, include_body: bool) -> None:
        request = urlparse(self.path)
        route = request.path
        if route == "/favicon.ico":
            self._send(204, b"", "image/x-icon", include_body=False)
            return
        if route in {"/decision", "/decision.html"}:
            try:
                self.server.publisher.decision.require_generation(
                    parse_generation_query(request.query)
                )
                body = DECISION_VIEW_HTML_PATH.read_bytes()
            except DecisionViewError as exc:
                self._send_decision_error(exc, include_body=include_body)
                return
            except OSError as exc:
                self._send_json(500, {"error": str(exc)}, include_body=include_body)
                return
            self._send(200, body, "text/html; charset=utf-8", include_body=include_body)
            return
        pages = {
            "/perception": VIEW_HTML_PATH,
            "/perception.html": VIEW_HTML_PATH,
            "/memory": MEMORY_VIEW_HTML_PATH,
            "/memory.html": MEMORY_VIEW_HTML_PATH,
        }
        if route in {"/", "/index.html"}:
            try:
                html = RUNTIME_VIEW_HTML_PATH.read_text(encoding="utf-8")
            except OSError as exc:
                self._send_json(500, {"error": str(exc)}, include_body=include_body)
                return
            decision_nav = self.server.publisher.decision_navigation_html(compact=False)
            self._send(
                200,
                html.replace("__DECISION_NAV__", decision_nav).encode("utf-8"),
                "text/html; charset=utf-8",
                include_body=include_body,
            )
            return
        if route in pages:
            try:
                html = pages[route].read_text(encoding="utf-8")
                html = html.replace(
                    "__DECISION_NAV__",
                    self.server.publisher.decision_navigation_html(compact=True),
                )
            except OSError as exc:
                self._send_json(500, {"error": str(exc)}, include_body=include_body)
                return
            self._send(
                200,
                html.encode("utf-8"),
                "text/html; charset=utf-8",
                include_body=include_body,
            )
            return
        if route == "/api/decision/latest":
            try:
                payload = self.server.publisher.decision.latest_payload(
                    generation=parse_generation_query(request.query)
                )
            except DecisionViewError as exc:
                self._send_decision_error(exc, include_body=include_body)
                return
            self._send_json(200, payload, include_body=include_body)
            return
        if route == "/api/decision/image":
            try:
                generation, transaction_id = parse_image_query(request.query)
                body, content_type = self.server.publisher.decision.image_response(
                    generation=generation,
                    transaction_id=transaction_id,
                )
            except DecisionViewError as exc:
                self._send_decision_error(exc, include_body=include_body)
                return
            self._send(200, body, content_type, include_body=include_body)
            return
        if route == "/api/health":
            self._send_json(
                200,
                self.server.publisher.health_payload(),
                include_body=include_body,
            )
            return
        if route == "/api/latest":
            body = self.server.publisher.perception.latest_json()
            if body is None:
                self._send_json(
                    503,
                    {"error": "no camera frame has been published yet"},
                    include_body=include_body,
                )
                return
            self._send(
                200,
                body,
                "application/json; charset=utf-8",
                include_body=include_body,
            )
            return
        if route == "/frame":
            requested_frame_id = parse_qs(request.query).get("v", [None])[0]
            frame = self.server.publisher.perception.frame(requested_frame_id)
            if frame is None:
                if requested_frame_id is not None:
                    self._send_json(
                        404,
                        {"error": "requested perception frame is no longer available"},
                        include_body=include_body,
                    )
                    return
                self._send_json(
                    503,
                    {"error": "no camera frame has been published yet"},
                    include_body=include_body,
                )
                return
            body, content_type = frame
            self._send(200, body, content_type, include_body=include_body)
            return
        self._send_json(404, {"error": "not found"}, include_body=include_body)

    def _send_decision_error(
        self,
        error: DecisionViewError,
        *,
        include_body: bool,
    ) -> None:
        self._send_json(
            error.status_code,
            decision_error_payload(self.server.publisher.decision, reason=error.reason),
            include_body=include_body,
        )


def _vehicle_view_port(vehicle_id: str) -> int:
    return 8500 + (zlib.crc32(vehicle_id.encode("utf-8")) % 500)


def _timestamp_ms() -> int:
    return int(time.time() * 1000)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)
