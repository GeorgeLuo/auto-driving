"""Shared runtime page hosting, independent of any one processing layer."""

from __future__ import annotations

import json
import os
import threading
import time
import webbrowser
import zlib
from pathlib import Path
from typing import Any, Callable
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
from .plugin_catalog import serve_plugin_catalog
from .vehicles import get_vehicle_status
from autonomy.decision_cycle.activation import STEPS
from autonomy.runtime.plugin_catalog import PLUGIN_CATALOG_PATH


VIEW_HOST = "127.0.0.1"
# Vehicle views take 8500–8999. This port is the viewer opened with no host.
VIEWER_PORT = 8473
NOT_CONNECTED_ERROR = "not connected to a host"
# Routes answered from a runtime host; a viewer that is not connected refuses them.
RUNTIME_HOST_ROUTES = frozenset({
    PLUGIN_CATALOG_PATH,
    "/api/latest",
    "/frame",
    "/api/decision/latest",
    "/api/decision/image",
})
RUNTIME_VIEW_HTML_PATH = Path(__file__).with_name("runtime_view.html")
MEMORY_VIEW_HTML_PATH = Path(__file__).with_name("memory_view.html")
DECISION_VIEW_HTML_PATH = Path(__file__).with_name("live_decision_view.html")


class RuntimeViewServer:
    """Serve peer runtime pages; retain the existing discovery and health contract."""

    def __init__(
        self,
        *,
        vehicle_id: str,
        automation_dir: Path | None,
        host: str = VIEW_HOST,
        port: int | None = None,
        run_id: str | None = None,
        worker_pid: int | None = None,
        decision_identity: dict[str, Any] | None = None,
        plugin_catalog: Callable | None = None,
        connected: bool = True,
    ) -> None:
        validate_loopback_host(host, owner="runtime view")
        self.vehicle_id = vehicle_id
        self.connected = connected
        self.plugin_catalog = plugin_catalog
        self.automation_dir = automation_dir
        self.host = host
        self.preferred_port = _vehicle_view_port(vehicle_id) if port is None else int(port)
        self.run_id = run_id
        self.worker_pid = int(worker_pid) if isinstance(worker_pid, int) else os.getpid()
        # Without an automation dir the server publishes no discovery record.
        self.record_path = None if automation_dir is None else automation_dir / VIEW_RECORD_NAME
        self.perception = PerceptionView(vehicle_id=vehicle_id)
        self.decision = DecisionView(vehicle_id=vehicle_id, identity=decision_identity)
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
        self._write_record("running")
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
            "record_path": None if self.record_path is None else str(self.record_path),
        }

    def health_payload(self) -> dict[str, Any]:
        return {
            **self.describe(),
            **self.perception.health_payload(),
            "decision": self.decision.health_payload(),
        }

    def connection_dot_html(self) -> str:
        """The splash-page dot: live while connected to a runtime host."""

        if self.connected:
            return '<span class="status-dot live"></span>'
        return f'<span class="status-dot error" title="{NOT_CONNECTED_ERROR}"></span>'

    def splash_navigation_html(self) -> str:
        """One entry per runtime page; none is available until connected."""

        if not self.connected:
            return "\n".join(
                _unavailable_nav(label, NOT_CONNECTED_ERROR)
                for label in ("Perception", "Memory", "Action", "Plugins")
            )
        return "\n".join((
            _nav_link("Perception", "/perception"),
            _nav_link("Memory", "/memory"),
            self.decision_navigation_html(),
            _nav_link("Plugins", "/plugins"),
        ))

    def decision_navigation_html(self) -> str:
        """Return a usable decision link or an explicit unavailable status."""

        page_url = self.decision.page_url()
        if page_url is None:
            return _unavailable_nav("Action", "Action view unavailable")
        return _nav_link("Action", page_url)

    def stop(self) -> None:
        httpd = self._httpd
        thread = self._thread
        if httpd is None:
            return
        stop_server_thread(httpd, thread)
        self.decision.stop()
        self._write_record("stopped")
        self._httpd = None
        self._thread = None

    def _write_record(self, status: str) -> None:
        if self.record_path is not None:
            _write_json(self.record_path, self.describe(status=status))


class _RuntimeHttpServer(LoopbackHTTPServer):
    publisher: RuntimeViewServer


class _RuntimeViewHandler(LoopbackHTTPRequestHandler):
    server: _RuntimeHttpServer

    def do_GET(self) -> None:
        self._handle_request(include_body=True)

    def do_HEAD(self) -> None:
        self._handle_request(include_body=False)

    def do_POST(self) -> None:
        route = urlparse(self.path).path
        if self._refuse_when_not_connected(route, include_body=True):
            return
        if route == PLUGIN_CATALOG_PATH:
            serve_plugin_catalog(self, self.server.publisher.plugin_catalog)
            return
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

    def _refuse_when_not_connected(self, route: str, *, include_body: bool) -> bool:
        """Answer a runtime-host route while no host is connected; True if answered."""

        if self.server.publisher.connected or route not in RUNTIME_HOST_ROUTES:
            return False
        self._send_json(503, {"ok": False, "error": NOT_CONNECTED_ERROR}, include_body=include_body)
        return True

    def _handle_request(self, *, include_body: bool) -> None:
        request = urlparse(self.path)
        route = request.path
        if self._refuse_when_not_connected(route, include_body=include_body):
            return
        if self._serve_shared_file(route, include_body=include_body):
            return
        if route == PLUGIN_CATALOG_PATH:
            serve_plugin_catalog(self, self.server.publisher.plugin_catalog, include_body=include_body)
            return
        if route == "/plugins":
            html = Path(__file__).with_name("plugin_catalog.html").read_text(encoding="utf-8")
            self._send(200, html.replace("__PLUGIN_STEPS__", json.dumps(list(STEPS))).encode("utf-8"),
                       "text/html; charset=utf-8", include_body=include_body)
            return
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
            publisher = self.server.publisher
            html = html.replace("__CONNECTION_DOT__", publisher.connection_dot_html())
            html = html.replace("__RUNTIME_NAV__", publisher.splash_navigation_html())
            self._send(
                200,
                html.encode("utf-8"),
                "text/html; charset=utf-8",
                include_body=include_body,
            )
            return
        if route in pages:
            try:
                html = pages[route].read_text(encoding="utf-8")
                html = html.replace(
                    "__DECISION_NAV__",
                    self.server.publisher.decision_navigation_html(),
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
        if route == "/api/hosts":
            self._send_json(
                200,
                {"hosts": connectable_hosts(get_vehicle_status())},
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


def serve_viewer() -> int:
    """Serve the runtime pages with no runtime host until interrupted."""

    server = RuntimeViewServer(
        vehicle_id="viewer",
        automation_dir=None,
        port=VIEWER_PORT,
        connected=False,
    ).start()
    print(open_view_message(server.url or ""), flush=True)
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        return 130
    finally:
        server.stop()


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


def connectable_hosts(status: dict[str, Any]) -> list[dict[str, Any]]:
    """List the runtime hosts in a ``vehicles status`` payload with a one-word state."""

    # A blocked host is named by the layer that blocks it.
    hosts = [
        {"vehicle_id": card["vehicle_id"], "state": card["readiness"]["blocking_layer"] or "ready"}
        for card in status["vehicles"]
    ]
    # vehicles status does not probe deployments outside Chase.
    hosts.extend(
        {"vehicle_id": deployment["vehicle_id"], "state": None}
        for deployment in status["other_local_deployments"]
    )
    return hosts


def _nav_link(label: str, href: str) -> str:
    return f'<a class="nav" href="{href}">{label}</a>'


def _unavailable_nav(label: str, reason: str) -> str:
    return f'<span class="nav unavailable" title="{reason}">{label}</span>'


def _vehicle_view_port(vehicle_id: str) -> int:
    return 8500 + (zlib.crc32(vehicle_id.encode("utf-8")) % 500)


def _timestamp_ms() -> int:
    return int(time.time() * 1000)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)
