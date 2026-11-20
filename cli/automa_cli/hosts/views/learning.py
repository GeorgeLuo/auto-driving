"""Each host's learning history, read from the host when a page asks for it.

The viewer serves one page for every host, so it keeps a ``LearningHistory``
per host. Each read takes the host's status now, records what changed in its
memory step, and answers the rows after the page's cursor. A host that stops
keeps its history, so a page still shows what it learned; a name never read
gets none.
"""

from __future__ import annotations

import threading
from collections import OrderedDict

from autonomy.runtime.http_errors import error_payload

from ..discovery import runtime_base_url
from ..errors import RuntimeHostError
from ..publications.host_publications import fetch_autonomy_status
from .learning_history import LearningHistory

_READ_ERRORS = (RuntimeHostError, ConnectionError, OSError, TypeError, ValueError)
MAX_HOSTS = 32


class HostLearning:
    """Hosts' learning histories, each fed by the reads that ask for it."""

    def __init__(self, *, timeout_s: float = 3.0) -> None:
        self.timeout_s = timeout_s
        self._hosts: OrderedDict[str, LearningHistory] = OrderedDict()
        self._lock = threading.Lock()

    def history(self, host_name: str, since: int = 0) -> dict[str, object]:
        """The host's history after ``since``. ``live`` is whether its run is going; ``error`` says why not."""

        try:
            host, session = self._host_status(host_name)
        except _READ_ERRORS as exc:
            with self._lock:
                history = self._hosts.get(host_name)
            if history is None:
                return error_payload(str(exc))
            return {**history.payload(since), "host_name": host_name, "live": False, "error": str(exc)}
        history = self._history(host_name)
        history.ingest(host)
        live = session == "running"
        return {**history.payload(since), "host_name": host_name, "live": live,
                "error": None if live else f"Host {host_name!r} is {session or 'not running'}."}

    def _host_status(self, host_name: str) -> tuple[dict[str, object], object]:
        """The host's autonomy status and its session's status (``running`` while a run goes)."""

        status = fetch_autonomy_status(runtime_base_url(host_name), timeout_s=self.timeout_s)
        host, session = status.get("host"), status.get("session")
        if status.get("http_status", 200) != 200 or not isinstance(host, dict):
            raise ConnectionError(str(status.get("error") or f"Host {host_name!r} did not report its status."))
        return host, session.get("status") if isinstance(session, dict) else None

    def _history(self, host_name: str) -> LearningHistory:
        with self._lock:
            if host_name not in self._hosts:
                self._hosts[host_name] = LearningHistory()
                if len(self._hosts) > MAX_HOSTS:
                    self._hosts.popitem(last=False)
            self._hosts.move_to_end(host_name)
            return self._hosts[host_name]
