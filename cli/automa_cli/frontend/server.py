"""The runtime viewer's HTTP server: the v2 page, its command routes and the workbench."""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Callable, Mapping
from urllib.parse import parse_qs, urljoin, urlparse
from urllib.request import urlopen

from .loopback_http import (
    LoopbackHTTPRequestHandler,
    LoopbackHTTPServer,
    local_write_error,
    read_json_object,
    start_server_thread,
    stop_server_thread,
    validate_loopback_host,
)
from .v2_page import V2_PAGE_PATH, serve_v2_file
from .workbench_view import WORKBENCH_CONTENT_SECURITY_POLICY, ReplayRunner, serve_workbench_route
from autonomy.runtime.http_errors import error_payload


VIEW_HOST = "127.0.0.1"
# The port ./cli/automa serves the viewer on; AUTOMA_VIEWER_PORT moves it (0: any free port).
VIEWER_PORT = int(os.environ.get("AUTOMA_VIEWER_PORT", "8473"))
# The workbench page and API, answered from a replay runner whether or not a host is connected.
WORKBENCH_PREFIX = "/workbench"
# A read route: the request's query parameters to a document, or to an image's bytes and content type.
Read = Callable[[Mapping[str, str]], "dict[str, object] | tuple[bytes, str]"]
# A write route: the request's JSON object to the command's result.
Write = Callable[[dict[str, object]], dict[str, object]]


class RuntimeViewServer:
    """Serve the v2 page, the viewer's command routes and the workbench.

    ``hosts`` lists the hosts ``/api/hosts`` answers. ``reads`` maps a GET route
    to what it serves for the request's query parameters: a document, or an
    image as its bytes and content type. ``writes`` maps a POST route to the
    command it runs with the request's JSON object. A document or result with
    ``ok`` false answers 400.
    A view serves only the routes it was built with. The workbench serves
    ``replay_runner``, or the one ``replay_runner_factory`` builds on the first
    visit; the server needs one of the two.
    """

    def __init__(
        self,
        *,
        host_name: str,
        host: str = VIEW_HOST,
        port: int = 0,
        hosts: Callable[[], list[dict[str, object]]] | None = None,
        reads: Mapping[str, Read] | None = None,
        writes: Mapping[str, Write] | None = None,
        replay_runner: ReplayRunner | None = None,
        replay_runner_factory: Callable[[], ReplayRunner] | None = None,
    ) -> None:
        validate_loopback_host(host, owner="runtime view")
        if replay_runner is None and replay_runner_factory is None:
            raise TypeError("RuntimeViewServer needs replay_runner or replay_runner_factory")
        self.host_name = host_name
        self.hosts = hosts
        self.reads = reads or {}
        self.writes = writes or {}
        self._replay_runner = replay_runner
        self._replay_runner_factory = replay_runner_factory
        self._replay_runner_lock = threading.Lock()
        self.host = host
        self.preferred_port = int(port)
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

    def replay_runner(self) -> ReplayRunner:
        """Build the viewer's shared replay runner on the first workbench visit."""

        with self._replay_runner_lock:
            if self._replay_runner is None:
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
        return self

    def health_payload(self) -> dict[str, object]:
        """What ``/api/health`` answers: which viewer this is, and where."""

        return {"ok": True, "host_name": self.host_name, "url": self.url, "pid": os.getpid(),
                "started_at_ms": self._started_at_ms}

    def stop(self) -> None:
        httpd = self._httpd
        thread = self._thread
        if httpd is None:
            return
        stop_server_thread(httpd, thread)
        if self._replay_runner is not None:
            self._replay_runner.close()
        self._httpd = None
        self._thread = None


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
        if self._serve_workbench(route, include_body=True):
            return
        if route in self.server.publisher.writes:
            self._serve_write(self.server.publisher.writes[route])
            return
        self._reject_write_method()

    def _serve_write(self, command: Write) -> None:
        """Run a write route's command with the request's JSON object."""

        error = local_write_error(self)
        if error is not None:
            self._send_json(*error)
            return
        # The command locks the record it changes. Holding one lock across every
        # write would make a stop wait for another host's command or for plugin arming.
        try:
            body = read_json_object(self, what="request body")
            result = command(body)
        except (OSError, ValueError, TypeError, RuntimeError) as exc:
            result = error_payload(str(exc))
        self._send_json(200 if result.get("ok") else 400, result)

    def _serve_read(self, read: Read, query: str, *, include_body: bool) -> None:
        """Answer a read route with what it reads for the query's parameters, each its first value."""

        try:
            answer = read({name: values[0] for name, values in parse_qs(query).items()})
        except (OSError, ValueError, TypeError, RuntimeError) as exc:
            answer = error_payload(str(exc))
        if isinstance(answer, tuple):
            body, content_type = answer
            self._send(200, body, content_type, include_body=include_body)
            return
        self._send_json(200 if answer.get("ok", True) else 400, answer, include_body=include_body)

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
        self._send_json(405, error_payload("method not allowed"))

    def _serve_workbench(self, route: str, *, include_body: bool) -> bool:
        """Answer a route under /workbench/ from the viewer's replay runner; True if answered."""

        if not route.startswith(WORKBENCH_PREFIX + "/"):
            return False
        runner = self.server.publisher.replay_runner()
        self.content_security_policy = WORKBENCH_CONTENT_SECURITY_POLICY
        if not serve_workbench_route(self, runner, route.removeprefix(WORKBENCH_PREFIX), include_body=include_body):
            self._send_json(404, error_payload("not found"), include_body=include_body)
        return True

    def _handle_request(self, *, include_body: bool) -> None:
        request = urlparse(self.path)
        route = request.path
        answered = (
            self._serve_workbench(route, include_body=include_body)
            or self._serve_shared_file(route, include_body=include_body)
            or serve_v2_file(self, route, include_body=include_body)
            or self._serve_page(route, include_body=include_body)
            or self._serve_api(route, request.query, include_body=include_body)
        )
        if not answered:
            self._send_json(404, error_payload("not found"), include_body=include_body)

    def _serve_page(self, route: str, *, include_body: bool) -> bool:
        """Send ``/`` to the v2 page; True if answered."""

        if route == "/favicon.ico":
            self._send(204, b"", "image/x-icon", include_body=include_body)
            return True
        if route not in {"/", "/index.html"}:
            return False
        self.send_response(302)
        self.send_header("Location", V2_PAGE_PATH)
        self.send_header("Content-Length", "0")
        self.end_headers()
        return True

    def _serve_api(self, route: str, query: str, *, include_body: bool) -> bool:
        """Answer an API route: health, the host list, or a read route; True if answered."""

        publisher = self.server.publisher
        if route == "/api/health":
            self._send_json(200, publisher.health_payload(), include_body=include_body)
        elif route == "/api/hosts":
            self._send_json(200, {"hosts": [] if publisher.hosts is None else publisher.hosts()}, include_body=include_body)
        elif route in publisher.reads:
            self._serve_read(publisher.reads[route], query, include_body=include_body)
        else:
            return False
        return True


def running_viewer_url(timeout_s: float = 0.5) -> str | None:
    """The URL of the viewer ``./cli/automa`` serves on ``VIEWER_PORT``, or None when none answers."""

    url = f"http://{VIEW_HOST}:{VIEWER_PORT}/"
    try:
        with urlopen(urljoin(url, "api/health"), timeout=timeout_s) as response:
            health = json.load(response)
    except (OSError, ValueError):
        return None
    return url if isinstance(health, dict) and health.get("host_name") == "viewer" else None


def _timestamp_ms() -> int:
    return int(time.time() * 1000)
