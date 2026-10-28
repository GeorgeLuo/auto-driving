"""The runtime viewer's HTTP server: its pages, their APIs and the workbench."""

from __future__ import annotations

import json
import os
import threading
import time
import zlib
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from .perception_view import (
    PerceptionView,
    VIEW_SCHEMA,
    VIEW_RECORD_NAME,
)
from .decision_view import (
    DecisionView,
    DecisionViewError,
    decision_error_payload,
    parse_generation_query,
    parse_image_query,
)

from .loopback_http import (
    PAGES_DIR,
    LoopbackHTTPRequestHandler,
    LoopbackHTTPServer,
    start_server_thread,
    stop_server_thread,
    validate_loopback_host,
)
from .plugin_catalog import serve_plugin_catalog
from .workbench_view import WORKBENCH_CONTENT_SECURITY_POLICY, ReplayRunner, serve_workbench_route
from autonomy.decision_cycle.activation import STEPS
from autonomy.runtime.plugin_catalog import PLUGIN_CATALOG_PATH


VIEW_HOST = "127.0.0.1"
# Host views take 8500–8999. This port is the viewer opened with no host.
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
# The workbench page and API, answered from a replay runner whether or not a host is connected.
WORKBENCH_PREFIX = "/workbench"
WORKBENCH_UNAVAILABLE_ERROR = "no replay runner"
HOME_PAGE_PATH = PAGES_DIR / "home" / "index.html"
PERCEPTION_PAGE_PATH = PAGES_DIR / "perception" / "index.html"
MEMORY_PAGE_PATH = PAGES_DIR / "memory" / "index.html"
DECISION_PAGE_PATH = PAGES_DIR / "decision" / "index.html"
PLUGINS_PAGE_PATH = PAGES_DIR / "plugins" / "index.html"


class RuntimeViewServer:
    """Serve peer runtime pages; retain the existing discovery and health contract.

    ``hosts`` lists the hosts the home page shows. The workbench serves
    ``replay_runner``, or one ``replay_runner_factory`` builds on the first
    visit; with neither, its routes answer 503.
    """

    def __init__(
        self,
        *,
        host_name: str,
        automation_dir: Path | None,
        host: str = VIEW_HOST,
        port: int | None = None,
        run_id: str | None = None,
        worker_pid: int | None = None,
        decision_identity: dict[str, Any] | None = None,
        plugin_catalog: Callable | None = None,
        connected: bool = True,
        hosts: Callable[[], list[dict[str, Any]]] | None = None,
        replay_runner: ReplayRunner | None = None,
        replay_runner_factory: Callable[[], ReplayRunner] | None = None,
    ) -> None:
        validate_loopback_host(host, owner="runtime view")
        self.host_name = host_name
        self.connected = connected
        self.hosts = hosts
        self._replay_runner = replay_runner
        self._replay_runner_factory = replay_runner_factory
        self._replay_runner_lock = threading.Lock()
        self.plugin_catalog = plugin_catalog
        self.automation_dir = automation_dir
        self.host = host
        self.preferred_port = _host_view_port(host_name) if port is None else int(port)
        self.run_id = run_id
        self.worker_pid = int(worker_pid) if isinstance(worker_pid, int) else os.getpid()
        # Without an automation dir the server publishes no discovery record.
        self.record_path = None if automation_dir is None else automation_dir / VIEW_RECORD_NAME
        self.perception = PerceptionView(host_name=host_name)
        self.decision = DecisionView(host_name=host_name, identity=decision_identity)
        self._httpd: _RuntimeHttpServer | None = None
        self._thread: threading.Thread | None = None
        self._started_at_ms: int | None = None

    @property
    def url(self) -> str | None:
        if self._httpd is None:
            return None
        return self._httpd.loopback_url

    @property
    def workbench_url(self) -> str | None:
        return f"{self.url}workbench/" if self.url is not None else None

    def replay_runner(self) -> ReplayRunner | None:
        """Build the viewer's shared replay runner on the first workbench visit."""

        with self._replay_runner_lock:
            if self._replay_runner is None and self._replay_runner_factory is not None:
                self._replay_runner = self._replay_runner_factory()
            return self._replay_runner

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
            name=f"automa-runtime-view-{self.host_name}",
        )
        self._write_record("running")
        return self

    def describe(self, *, status: str = "running") -> dict[str, Any]:
        # Keep the established discovery record/schema for existing CLI readers.
        return {
            "schema": VIEW_SCHEMA,
            "host_name": self.host_name,
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
        if self._replay_runner is not None:
            self._replay_runner.close()
        self.decision.stop()
        self._write_record("stopped")
        self._httpd = None
        self._thread = None

    def _write_record(self, status: str) -> None:
        if self.record_path is not None:
            _write_json(self.record_path, self.describe(status=status))


class _RuntimeHttpServer(LoopbackHTTPServer):
    # A workbench visit queues its styles and scripts while replay work is running.
    request_queue_size = 128
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
        if self._serve_workbench(route, include_body=True):
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

    def _serve_workbench(self, route: str, *, include_body: bool) -> bool:
        """Answer a route under /workbench/ from the viewer's replay runner; True if answered."""

        if not route.startswith(WORKBENCH_PREFIX + "/"):
            return False
        runner = self.server.publisher.replay_runner()
        if runner is None:
            self._send_json(503, {"ok": False, "error": WORKBENCH_UNAVAILABLE_ERROR}, include_body=include_body)
            return True
        self.content_security_policy = WORKBENCH_CONTENT_SECURITY_POLICY
        if not serve_workbench_route(self, runner, route.removeprefix(WORKBENCH_PREFIX), include_body=include_body):
            self._send_json(404, {"error": "not found"}, include_body=include_body)
        return True

    def _handle_request(self, *, include_body: bool) -> None:
        request = urlparse(self.path)
        route = request.path
        if self._refuse_when_not_connected(route, include_body=include_body):
            return
        if self._serve_workbench(route, include_body=include_body):
            return
        if self._serve_shared_file(route, include_body=include_body):
            return
        if route == PLUGIN_CATALOG_PATH:
            serve_plugin_catalog(self, self.server.publisher.plugin_catalog, include_body=include_body)
            return
        if route == "/plugins":
            html = PLUGINS_PAGE_PATH.read_text(encoding="utf-8")
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
                body = DECISION_PAGE_PATH.read_bytes()
            except DecisionViewError as exc:
                self._send_decision_error(exc, include_body=include_body)
                return
            except OSError as exc:
                self._send_json(500, {"error": str(exc)}, include_body=include_body)
                return
            self._send(200, body, "text/html; charset=utf-8", include_body=include_body)
            return
        pages = {
            "/perception": PERCEPTION_PAGE_PATH,
            "/perception.html": PERCEPTION_PAGE_PATH,
            "/memory": MEMORY_PAGE_PATH,
            "/memory.html": MEMORY_PAGE_PATH,
        }
        if route in {"/", "/index.html"}:
            try:
                html = HOME_PAGE_PATH.read_text(encoding="utf-8")
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
            hosts = self.server.publisher.hosts
            self._send_json(200, {"hosts": [] if hosts is None else hosts()}, include_body=include_body)
            return
        if route == "/api/latest":
            body = self.server.publisher.perception.latest_json()
            if body is None:
                self._send_json(
                    503,
                    {"error": self.server.publisher.perception.unavailable_reason},
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
                    {"error": self.server.publisher.perception.unavailable_reason},
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


def _nav_link(label: str, href: str) -> str:
    return f'<a class="nav" href="{href}">{label}</a>'


def _unavailable_nav(label: str, reason: str) -> str:
    return f'<span class="nav unavailable" title="{reason}">{label}</span>'


def _host_view_port(host_name: str) -> int:
    return 8500 + (zlib.crc32(host_name.encode("utf-8")) % 500)


def _timestamp_ms() -> int:
    return int(time.time() * 1000)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)
