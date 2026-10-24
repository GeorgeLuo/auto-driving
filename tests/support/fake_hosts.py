"""Runtime host addresses that refuse, hang, or answer with a fixed reply."""

from __future__ import annotations

import socket
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Iterator


def closed_url() -> str:
    """An address no host listens on."""

    with socket.socket() as closed:
        closed.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{closed.getsockname()[1]}"


@contextmanager
def hung_url() -> Iterator[str]:
    """A host that takes connections and never answers."""

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(8)
        yield f"http://127.0.0.1:{listener.getsockname()[1]}"


@contextmanager
def answering(body: bytes | None, status: int = 200) -> Iterator[str]:
    """A host that answers every GET with ``body``, or closes without a reply when it is None."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if body is None:
                return
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
