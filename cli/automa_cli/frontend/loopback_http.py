"""Shared HTTP transport mechanics for Automa's local loopback pages."""

from __future__ import annotations

import json
import re
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from autonomy.runtime.plugin_catalog import catalog_write_error


LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
DEFAULT_CONTENT_SECURITY_POLICY = (
    "default-src 'self'; img-src 'self'; connect-src 'self'; "
    "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'"
)
STATIC_CONTENT_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
}
# One directory per page; ``shared`` holds the files every page may load,
# served read-only under /static/shared/.
PAGES_DIR = Path(__file__).with_name("pages")
SHARED_PAGE_DIR = PAGES_DIR / "shared"
SHARED_STATIC_PATTERN = re.compile(r"^/static/shared/([a-z]+\.(?:css|js))$")


def local_write_error(handler: BaseHTTPRequestHandler) -> tuple[int, dict[str, Any]] | None:
    """Refuse a POST unless it names this server by a loopback host and comes from its own origin."""

    authority = handler.headers.get("Host", "")
    try:
        address = urlsplit(f"http://{authority}")
        local = (address.hostname in LOOPBACK_HOSTS
                 and address.port == handler.server.server_port
                 and address.username is None and address.password is None
                 and not address.path and not address.query and not address.fragment)
    except ValueError:
        local = False
    if not local:
        return 403, {"ok": False, "status": "failed", "error": "writes require a loopback host"}
    return catalog_write_error(handler.headers, origin=f"http://{authority}")


def read_json_object(handler: BaseHTTPRequestHandler, *, what: str) -> dict[str, Any]:
    """The request's JSON body, which must be an object. Raises ``ValueError`` otherwise."""

    size = int(handler.headers.get("Content-Length", "0"))
    if size < 0:
        raise ValueError("negative content length")
    payload = json.loads(handler.rfile.read(size))
    if not isinstance(payload, dict):
        raise ValueError(f"{what} must be an object")
    return payload


def validate_loopback_host(host: str, *, owner: str) -> str:
    """Return an allowed loopback host or reject the bind boundary."""

    if host not in LOOPBACK_HOSTS:
        raise ValueError(f"{owner} must bind to a loopback address")
    return host


def start_server_thread(server: ThreadingHTTPServer, *, name: str) -> threading.Thread:
    """Start a daemon serving thread for a configured loopback server."""

    thread = threading.Thread(target=server.serve_forever, name=name, daemon=True)
    thread.start()
    return thread


def stop_server_thread(
    server: ThreadingHTTPServer,
    thread: threading.Thread | None,
) -> None:
    """Stop, close, and briefly join a loopback serving thread."""

    server.shutdown()
    server.server_close()
    if thread is not None:
        thread.join(timeout=1.0)


class LoopbackHTTPServer(ThreadingHTTPServer):
    """Threaded local HTTP transport with the repository's bind behavior."""

    daemon_threads = True
    allow_reuse_address = True

    @classmethod
    def bind_with_ephemeral_fallback(
        cls,
        *,
        host: str,
        preferred_port: int,
        handler: type[BaseHTTPRequestHandler],
    ) -> "LoopbackHTTPServer":
        server_type = cls
        if host == "::1":
            server_type = type(
                f"{cls.__name__}IPv6",
                (cls,),
                {"address_family": socket.AF_INET6},
            )
        try:
            return server_type((host, preferred_port), handler)
        except OSError:
            if preferred_port == 0:
                raise
            return server_type((host, 0), handler)

    @property
    def loopback_url(self) -> str:
        host, port = self.server_address[:2]
        display_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
        if ":" in display_host:
            display_host = f"[{display_host}]"
        return f"http://{display_host}:{port}/"


class LoopbackHTTPRequestHandler(BaseHTTPRequestHandler):
    """Shared response framing and security headers for loopback pages."""

    content_security_policy = DEFAULT_CONTENT_SECURITY_POLICY
    json_indent: int | None = None

    def _send_json(
        self,
        status: int,
        payload: dict[str, Any],
        *,
        include_body: bool = True,
    ) -> None:
        if self.json_indent is None:
            body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode(
                "utf-8"
            )
        else:
            body = json.dumps(
                payload,
                indent=self.json_indent,
                sort_keys=True,
            ).encode("utf-8")
        self._send(
            status,
            body,
            "application/json; charset=utf-8",
            include_body=include_body,
        )

    def _send(
        self,
        status: int,
        body: bytes,
        content_type: str,
        *,
        include_body: bool = True,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", self.content_security_policy)
        self.end_headers()
        if include_body:
            self.wfile.write(body)

    def _serve_shared_file(self, route: str, *, include_body: bool) -> bool:
        """Serve the shared page file ``route`` names; False leaves the route to the caller."""

        match = SHARED_STATIC_PATTERN.match(route)
        if match is None:
            return False
        path = SHARED_PAGE_DIR / match.group(1)
        try:
            body = path.read_bytes()
        except FileNotFoundError:
            return False
        self._send(200, body, STATIC_CONTENT_TYPES[path.suffix], include_body=include_body)
        return True

    def log_message(self, format: str, *args: Any) -> None:
        return None
