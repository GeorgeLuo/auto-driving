"""Shared HTTP transport mechanics for Automa's local loopback pages."""

from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
DEFAULT_CONTENT_SECURITY_POLICY = (
    "default-src 'self'; img-src 'self'; connect-src 'self'; "
    "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'"
)


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

    def log_message(self, format: str, *args: Any) -> None:
        return None
