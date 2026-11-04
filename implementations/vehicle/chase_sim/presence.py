"""Keep a host on the simulator's agent list for as long as the host runs."""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

from .metrics_ws import MetricsUiConnection, MetricsUiWsClient

logger = logging.getLogger(__name__)

RETRY_S = 2.0


class MetricsUiPresence:
    """One agent socket registered as ``client``, reopened whenever it drops.

    Metrics UI lists the socket to other agents (``list_agents``), which is how
    ``hosts status`` tells that a host is attached. The socket only listens:
    it drains what the server broadcasts to agents until the server closes it.
    """

    def __init__(self, url: str, client: dict[str, Any], *, timeout_s: float = 5.0,
                 retry_s: float = RETRY_S) -> None:
        self.url = url
        self.client = client
        self.timeout_s = float(timeout_s)
        self.retry_s = float(retry_s)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._ws: MetricsUiConnection | None = None
        self._thread = threading.Thread(target=self._hold, name="metrics-ui-presence", daemon=True)

    def __enter__(self) -> MetricsUiPresence:
        self._thread.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            self._stop.set()
            ws = self._ws
        if ws is not None:
            # Closing the socket interrupts the blocking read.
            ws.close()
        if self._thread.ident is not None:
            self._thread.join(timeout=self.timeout_s)

    def _hold(self) -> None:
        registrar = MetricsUiWsClient(self.url, timeout_s=self.timeout_s)
        reported: str | None = None
        while not self._stop.is_set():
            ws = MetricsUiConnection(self.url, timeout_s=self.timeout_s)
            try:
                ws.connect()
                with self._lock:
                    if self._stop.is_set():
                        return
                    self._ws = ws
                ws.send_json({"type": "register", "role": "agent", "client": self.client})
                registrar.wait_for_registration(ws, time.monotonic() + self.timeout_s)
                logger.info("Attached to Metrics UI at %s", self.url)
                reported = None
                ws.set_read_timeout(None)
                while True:
                    ws.recv_json()
            except Exception as exc:
                # Any failure retries, so the host never silently stops attaching.
                # Report each new reason once, not every retry.
                if not self._stop.is_set() and str(exc) != reported:
                    reported = str(exc)
                    logger.warning("Not attached to Metrics UI at %s: %s; retrying every %gs",
                                   self.url, exc, self.retry_s)
            finally:
                with self._lock:
                    self._ws = None
                ws.close()
            self._stop.wait(self.retry_s)
