"""Shared runtime page hosting, independent of any one processing layer."""

from __future__ import annotations

import json
import os
import re
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
from .host_publications import LATEST_JSON_PATH
from .plugin_catalog import serve_plugin_catalog
from .runtime_hosts import RuntimeHostError, ensure_chase_host
from autonomy.decision_cycle.activation import STEPS
from autonomy.runtime.plugin_catalog import PLUGIN_CATALOG_PATH


VIEW_HOST = "127.0.0.1"
_REPO_ROOT = Path(__file__).resolve().parents[2]
_AUTOMA = _REPO_ROOT / "cli" / "automa"
_STATUS_SCHEMA = "automa_vehicle_status_v1"
_PERCEPTION_LIVE_SCHEMA = "vehicle_perception_live_v0"
# The status command's own probe defaults to 5s. This only stops a hung process.
_STATUS_LIMIT_S = 20
_VEHICLE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_FEED_REFRESH_S = 0.5
FLOAT_PAGE_DIR = Path(__file__).with_name("workbench_page")
FLOAT_FILES = {
    "/static/float.css": ("float.css", "text/css; charset=utf-8"),
    "/static/js/float.js": ("js/float.js", "text/javascript; charset=utf-8"),
    "/static/js/automation.js": ("js/automation.js", "text/javascript; charset=utf-8"),
}
# Vehicle views take 8500–8999. This port is the viewer opened with no host.
VIEWER_PORT = 8473
_OBSERVE_RUN_TIMEOUT_S = 20.0
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


def _command_json(args: list[str]) -> dict[str, Any] | None:
    try:
        completed = subprocess.run(
            [sys.executable, str(_AUTOMA), *args],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=_STATUS_LIMIT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    stdout = completed.stdout.strip()
    if not stdout:
        return None
    try:
        payload = json.loads(stdout.splitlines()[-1])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def probe_perception_host(vehicle_id: str) -> dict[str, Any]:
    """Run one ``vehicles stream perception --once --json`` probe."""

    payload = _command_json(
        ["vehicles", "stream", "perception", "--id", vehicle_id, "--once", "--json"]
    )
    if isinstance(payload, dict) and payload.get("schema") == _PERCEPTION_LIVE_SCHEMA:
        return payload
    return {"error": "perception stream is not live"}


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
        self.host_base_url: str | None = None
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
        self._publish_record = False
        self._feed_lock = threading.Lock()
        self._feed_stop: threading.Event | None = None
        self._feed_thread: threading.Thread | None = None
        self._feed_generation = 0

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
        # Attaching later keeps that choice: this server is a reader, not the record.
        if self.host_attached:
            self._publish_record = True
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

    def connect_host(self, vehicle_id: str) -> dict[str, Any]:
        """Attach this server to a running host, after one live perception probe."""

        probe = probe_perception_host(vehicle_id)
        if probe.get("status") != "live":
            error = str(probe.get("error") or "perception stream is not live")
            return {"ok": False, "error": error.splitlines()[0].strip()[:240]}
        endpoint = str(probe.get("endpoint") or "")
        if not endpoint.endswith(LATEST_JSON_PATH):
            return {"ok": False, "error": "perception stream did not name a host"}
        base_url = endpoint[: -len(LATEST_JSON_PATH)].rstrip("/")
        from .streaming import _ViewFeed

        with self._feed_lock:
            self._stop_feed()
            feed = _ViewFeed(vehicle_id, base_url=base_url, timeout_s=3.0, server=self)
            if feed.server is None or feed.decision is None:
                return {"ok": False, "error": feed.error or "perception stream is not live"}
            self.vehicle_id = vehicle_id
            self.perception.vehicle_id = vehicle_id
            self.host_attached = True
            self.host_base_url = base_url
            self._feed_generation += 1
            generation = self._feed_generation
            stop = threading.Event()
            self._feed_stop = stop
            thread = threading.Thread(
                target=self._run_feed,
                args=(feed, stop, generation),
                name=f"automa-view-feed-{vehicle_id}",
                daemon=True,
            )
            self._feed_thread = thread
            thread.start()
        return {"ok": True, "vehicle_id": vehicle_id}

    def _host_session(self) -> dict[str, Any] | None:
        if not self.host_attached or not self.host_base_url:
            return None
        from autonomy.runtime.client import RuntimeClient

        status = RuntimeClient(self.host_base_url, timeout_s=2.0).status()
        session = status.get("session") if isinstance(status, dict) else None
        return session if isinstance(session, dict) else None

    def automation_state(self) -> dict[str, Any]:
        """Whether the attached host session is applying automation."""

        try:
            session = self._host_session()
        except RuntimeError:
            return {"ok": True, "running": False, "automation": False}
        if session is None or session.get("status") != "running":
            return {"ok": True, "running": False, "automation": False, "vehicle_id": self.vehicle_id}
        configuration = session.get("configuration") if isinstance(session.get("configuration"), dict) else {}
        mode = configuration.get("mode")
        return {
            "ok": True,
            "running": True,
            "automation": mode == "autonomy",
            "mode": mode,
            "vehicle_id": self.vehicle_id,
        }

    def set_automation(self, enabled: bool) -> dict[str, Any]:
        """Switch the running session between autonomy and observe-only.

        The host process stays up. Its current run is replaced with the same
        decision bound and the requested mode.
        """

        from autonomy.runtime.client import RuntimeClient
        from autonomy.runtime.session import RunConfiguration

        try:
            session = self._host_session()
        except RuntimeError as exc:
            error = str(exc).splitlines()[0].strip()[:240]
            return {"ok": False, "error": error or "automation did not change"}
        if session is None or session.get("status") != "running":
            return {"ok": False, "error": "no automation session is running"}
        configuration = session.get("configuration") if isinstance(session.get("configuration"), dict) else {}
        mode = "autonomy" if enabled else "observe_only"
        if configuration.get("mode") == mode:
            return self.automation_state()
        decisions = configuration.get("num_decisions")
        if type(decisions) is not int or decisions < 0:
            decisions = 0
        try:
            RuntimeClient(self.host_base_url or "", timeout_s=_OBSERVE_RUN_TIMEOUT_S).start(
                RunConfiguration(mode=mode, num_decisions=decisions)
            )
        except RuntimeError as exc:
            settled = self.automation_state()
            if settled.get("running") and bool(settled.get("automation")) is enabled:
                return settled
            error = str(exc).splitlines()[0].strip()[:240]
            return {"ok": False, "error": error or "automation did not change"}
        return self.automation_state()

    def create_host(
        self,
        vehicle_id: str,
        on_status: Any = None,
    ) -> dict[str, Any]:
        """Start this vehicle's Chase host and wait until it answers."""

        try:
            ensure_chase_host(vehicle_id, on_status=on_status)
        except RuntimeHostError as exc:
            error = str(exc).splitlines()[0].strip()[:240]
            return {"ok": False, "error": error or "the host did not start"}
        return {"ok": True, "vehicle_id": vehicle_id}

    def start_run(
        self,
        vehicle_id: str,
        on_status: Any = None,
    ) -> dict[str, Any]:
        """Start an observe-only session on the running host, then attach once perception is live."""

        from autonomy.runtime.client import RuntimeClient
        from autonomy.runtime.session import RunConfiguration

        from .host_publications import fetch_observation_publication
        from .runtime_hosts import _log_offset, _new_log_lines, runtime_base_url

        try:
            base_url = runtime_base_url(vehicle_id)
        except RuntimeHostError as exc:
            error = str(exc).splitlines()[0].strip()[:240]
            return {"ok": False, "error": error or "the run did not start"}
        offset = _log_offset(vehicle_id)
        client = RuntimeClient(base_url, timeout_s=3.0)
        try:
            client.start(RunConfiguration(mode="observe_only", num_decisions=0))
        except RuntimeError as exc:
            error = str(exc).splitlines()[0].strip()[:240]
            return {"ok": False, "error": error or "the run did not start"}

        def emit_log() -> None:
            nonlocal offset
            if on_status is None:
                return
            offset, lines = _new_log_lines(vehicle_id, offset)
            for line in lines:
                on_status(line)

        deadline = time.monotonic() + _OBSERVE_RUN_TIMEOUT_S
        while time.monotonic() < deadline:
            emit_log()
            try:
                publication = fetch_observation_publication(base_url, timeout_s=2.0)
            except (OSError, ConnectionError, TimeoutError, ValueError, RuntimeError):
                publication = None
            if isinstance(publication, dict) and publication.get("health") == "healthy":
                emit_log()
                return self.connect_host(vehicle_id)
            try:
                session = client.status().get("session") or {}
            except RuntimeError:
                session = {}
            if session.get("status") in {"error", "stopped"}:
                time.sleep(0.1)
                emit_log()
                return {"ok": False, "error": "the run stopped before perception was live"}
            time.sleep(0.1)
        emit_log()
        return {"ok": False, "error": "The observation publication health is 'warming'."}

    def create_release(self, vehicle_id: str) -> dict[str, Any]:
        """Package this vehicle's controller release."""

        from .deploy import update_vehicle_autonomy

        result = update_vehicle_autonomy(vehicle_id=vehicle_id)
        if result.exit_code != 0:
            error = str(result.message or "the release was not created").splitlines()[0].strip()[:240]
            return {"ok": False, "error": error or "the release was not created"}
        return {"ok": True, "vehicle_id": vehicle_id}

    def _run_feed(self, feed: Any, stop: threading.Event, generation: int) -> None:
        while not stop.is_set() and generation == self._feed_generation:
            publication, _error = feed.read_observation()
            if stop.is_set() or generation != self._feed_generation:
                return
            feed.publish(publication)
            stop.wait(_FEED_REFRESH_S)

    def _stop_feed(self) -> None:
        stop = self._feed_stop
        thread = self._feed_thread
        self._feed_generation += 1
        self._feed_stop = None
        self._feed_thread = None
        if stop is not None:
            stop.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=_STATUS_LIMIT_S)

    def stop(self) -> None:
        self._stop_feed()
        httpd = self._httpd
        thread = self._thread
        if httpd is None:
            return
        stop_server_thread(httpd, thread)
        self.decision.stop()
        if self._publish_record:
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
        route = urlparse(self.path).path
        if route == "/automation":
            body = self._read_json_object(limit=4096)
            enabled = body.get("automation") if isinstance(body, dict) else None
            if type(enabled) is not bool:
                self._send_json(400, {"ok": False, "error": "automation must be true or false"})
                return
            result = self.server.publisher.set_automation(enabled)
            self._send_json(200 if result.get("ok") else 409, result)
            return
        if route in {"/connect", "/host", "/release", "/run"}:
            vehicle_id = self._posted_vehicle_id()
            if vehicle_id is None:
                self._send_json(400, {"ok": False, "error": "vehicle id is not valid"})
                return
            publisher = self.server.publisher
            if route == "/host":
                self._stream_json(lambda emit: publisher.create_host(vehicle_id, on_status=emit))
                return
            if route == "/run":
                self._stream_json(lambda emit: publisher.start_run(vehicle_id, on_status=emit))
                return
            if route == "/connect":
                result = publisher.connect_host(vehicle_id)
            else:
                result = publisher.create_release(vehicle_id)
            self._send_json(200 if result.get("ok") else 409, result)
            return
        if route == PLUGIN_CATALOG_PATH:
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

    def _stream_json(self, produce: Callable[[Callable[[str], None]], dict[str, Any]]) -> None:
        self.protocol_version = "HTTP/1.1"
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Connection", "close")
        self.end_headers()

        def emit(line: str) -> None:
            body = (json.dumps({"line": line}, separators=(",", ":")) + "\n").encode("utf-8")
            self._write_chunk(body)

        try:
            result = produce(emit)
            done = (json.dumps(result, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")
            self._write_chunk(done)
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except OSError:
            return

    def _write_chunk(self, payload: bytes) -> None:
        self.wfile.write(f"{len(payload):X}\r\n".encode("ascii"))
        self.wfile.write(payload)
        self.wfile.write(b"\r\n")
        self.wfile.flush()

    def _posted_vehicle_id(self) -> str | None:
        body = self._read_json_object(limit=4096)
        vehicle_id = body.get("vehicle_id") if isinstance(body, dict) else None
        if not isinstance(vehicle_id, str) or _VEHICLE_ID.fullmatch(vehicle_id) is None:
            return None
        return vehicle_id

    def _read_json_object(self, *, limit: int) -> dict[str, Any] | None:
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return None
        if size < 0 or size > limit:
            return None
        raw = self.rfile.read(size) if size else b""
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

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
        if route == "/automation":
            self._send_json(
                200,
                self.server.publisher.automation_state(),
                include_body=include_body,
            )
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
