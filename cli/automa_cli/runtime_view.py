"""Shared runtime page hosting, independent of any one processing layer."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
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
from autonomy.decision_cycle.activation import STEPS
from autonomy.runtime.plugin_catalog import PLUGIN_CATALOG_PATH


VIEW_HOST = "127.0.0.1"
_REPO_ROOT = Path(__file__).resolve().parents[2]
_AUTOMA = _REPO_ROOT / "cli" / "automa"
_STATUS_SCHEMA = "automa_vehicle_status_v1"
# The status command's own probe defaults to 5s. This only stops a hung process.
_STATUS_LIMIT_S = 20
FLOAT_PAGE_DIR = Path(__file__).with_name("workbench_page")
FLOAT_FILES = {
    "/static/float.css": ("float.css", "text/css; charset=utf-8"),
    "/static/js/float.js": ("js/float.js", "text/javascript; charset=utf-8"),
}
# Vehicle views take 8500–8999. This port is the viewer opened with no host.
VIEWER_PORT = 8473
DISCONNECTED_HOST_ERROR = "not connected to a host"
RUNTIME_VIEW_HTML_PATH = Path(__file__).with_name("runtime_view.html")
MEMORY_VIEW_HTML_PATH = Path(__file__).with_name("memory_view.html")
DECISION_VIEW_HTML_PATH = Path(__file__).with_name("live_decision_view.html")


def known_environments() -> dict[str, Any]:
    """Run ``vehicles status --json`` and return its payload, or an error object."""

    try:
        completed = subprocess.run(
            [sys.executable, str(_AUTOMA), "vehicles", "status", "--json"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=_STATUS_LIMIT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"error": "vehicles status timed out"}
    except OSError as exc:
        return {"error": str(exc)}
    stdout = completed.stdout.strip()
    try:
        payload = json.loads(stdout) if stdout else None
    except json.JSONDecodeError:
        payload = None
    if (
        isinstance(payload, dict)
        and payload.get("schema") == _STATUS_SCHEMA
        and isinstance(payload.get("vehicles"), list)
    ):
        return payload
    detail = completed.stderr.strip() or stdout or f"vehicles status exited {completed.returncode}"
    return {"error": detail.splitlines()[0].strip()[:240]}


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
        decision_identity: dict[str, Any] | None = None,
        plugin_catalog: Callable | None = None,
        host_attached: bool = True,
    ) -> None:
        validate_loopback_host(host, owner="runtime view")
        self.vehicle_id = vehicle_id
        self.host_attached = host_attached
        self.plugin_catalog = plugin_catalog
        self.automation_dir = automation_dir
        self.host = host
        self.preferred_port = _vehicle_view_port(vehicle_id) if port is None else int(port)
        self.run_id = run_id
        self.worker_pid = int(worker_pid) if isinstance(worker_pid, int) else os.getpid()
        self.record_path = automation_dir / VIEW_RECORD_NAME
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
        # A viewer with no host is not a vehicle's published view.
        if self.host_attached:
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

        if not self.host_attached:
            if compact:
                return '<a class="nav" href="/decision">Action</a>'
            return '<a href="/decision">Action</a>'
        page_url = self.decision.page_url()
        if page_url is not None:
            if compact:
                return f'<a class="nav" href="{page_url}">Action</a>'
            return f'<a href="{page_url}">Action</a>'
        if compact:
            return (
                '<span class="nav unavailable" title="Action view unavailable">Action</span>'
            )
        return '<span class="decision-unavailable">Action view unavailable</span>'

    def host_status_html(self) -> str:
        """The splash-page dot for whether this viewer has a host."""

        dot = "live" if self.host_attached else "error"
        return f'<span class="status-dot {dot}"></span>'

    def stop(self) -> None:
        httpd = self._httpd
        thread = self._thread
        if httpd is None:
            return
        stop_server_thread(httpd, thread)
        self.decision.stop()
        if self.host_attached:
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
        if urlparse(self.path).path == PLUGIN_CATALOG_PATH:
            if not self.server.publisher.host_attached:
                self._send_disconnected(include_body=True)
                return
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

    def _handle_request(self, *, include_body: bool) -> None:
        request = urlparse(self.path)
        route = request.path
        asset = FLOAT_FILES.get(route)
        if asset is not None:
            relative, content_type = asset
            try:
                body = (FLOAT_PAGE_DIR / relative).read_bytes()
            except OSError as exc:
                self._send_json(500, {"error": str(exc)}, include_body=include_body)
                return
            self._send(200, body, content_type, include_body=include_body)
            return
        if route == "/connect":
            payload = known_environments()
            status = 200 if payload.get("schema") == _STATUS_SCHEMA else 502
            self._send_json(status, payload, include_body=include_body)
            return
        if not self.server.publisher.host_attached and route in {
            PLUGIN_CATALOG_PATH,
            "/api/decision/latest",
            "/api/decision/image",
            "/api/latest",
            "/frame",
        }:
            self._send_disconnected(include_body=include_body)
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
            if not self.server.publisher.host_attached:
                try:
                    body = DECISION_VIEW_HTML_PATH.read_bytes()
                except OSError as exc:
                    self._send_json(500, {"error": str(exc)}, include_body=include_body)
                    return
                self._send(200, body, "text/html; charset=utf-8", include_body=include_body)
                return
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
            html = html.replace("__DECISION_NAV__", decision_nav)
            html = html.replace("__HOST_STATUS__", self.server.publisher.host_status_html())
            html = html.replace(
                "__HOST_CLASS__",
                "" if self.server.publisher.host_attached else "disconnected",
            )
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

    def _send_disconnected(self, *, include_body: bool) -> None:
        self._send_json(
            503,
            {"ok": False, "error": DISCONNECTED_HOST_ERROR},
            include_body=include_body,
        )

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


def serve_unattached_viewer() -> int:
    """Serve the runtime pages with no vehicle host and stay in the foreground."""

    directory = tempfile.TemporaryDirectory(prefix="automa-viewer-")
    server = RuntimeViewServer(
        vehicle_id="viewer",
        automation_dir=Path(directory.name),
        port=VIEWER_PORT,
        host_attached=False,
    )
    try:
        server.start()
        print(_browser_open_message(server.url or ""), flush=True)
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        return 130
    finally:
        server.stop()
        directory.cleanup()
    return 0


def _browser_open_message(url: str) -> str:
    """Attempt the same browser launch as ``automation run --open-view``."""

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


def _vehicle_view_port(vehicle_id: str) -> int:
    return 8500 + (zlib.crc32(vehicle_id.encode("utf-8")) % 500)


def _timestamp_ms() -> int:
    return int(time.time() * 1000)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)
