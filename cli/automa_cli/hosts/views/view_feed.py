"""Feed a local runtime view from a host's publications."""

from __future__ import annotations

from contextlib import suppress
from typing import Any

from ...frontend.server import RuntimeViewServer
from .decision_live import DecisionViewAdapter
from .observation import ObservationPublisher
from ..publications.frame_publication import health_error
from ..publications.host_publications import TransactionCache, fetch_frame_sample
from ..publications.plugin_catalog import PluginCatalogClient
from .runtime_view import runtime_view_server
from .view_discovery import runtime_view_dir


class ViewFeed:
    """Feed all runtime pages from one host frame read, with telemetry beside it.

    ``hosts viewer`` and a terminal stream each run one. Its failures end only this view.
    """

    def __init__(self, host_name: str, *, base_url: str, timeout_s: float) -> None:
        runtime_dir = runtime_view_dir(host_name)
        runtime_dir.mkdir(parents=True, exist_ok=True)
        self.base_url = base_url
        self.timeout_s = timeout_s
        self.server: RuntimeViewServer | None = None
        self.decision: DecisionViewAdapter | None = None
        self.error: str | None = None
        self.decision_error: str | None = None
        self._observation_cache = TransactionCache()
        self._observation: tuple[dict[str, Any], bytes | None] | None = None
        self.observation: ObservationPublisher | None = None
        try:
            # Assigned before it starts, so stop() also releases a partial start.
            self.server = runtime_view_server(
                host_name=host_name,
                automation_dir=runtime_dir,
                plugin_catalog=PluginCatalogClient(base_url, timeout_s=timeout_s),
            )
            self.server.start()
            self.observation = ObservationPublisher(self.server.perception)
            self.decision = DecisionViewAdapter(
                host_name=host_name, base_url=base_url, view_server=self.server, timeout_s=timeout_s,
            )
        except Exception as exc:  # noqa: BLE001 - a failed view ends only this view
            self.error = f"{type(exc).__name__}: {exc}"
            # Releasing a partial start can fail the same way; the first failure is the one reported.
            with suppress(Exception):
                self.stop()

    @property
    def url(self) -> str | None:
        return self.server.url if self.server is not None else None

    def state(self) -> tuple[str | None, str | None, str | None, str | None]:
        """This view's URL, error, decision page path, and decision error.

        Reading these starts no request. A stream copies them before its
        renderer runs.
        """

        page = None
        if self.server is not None and self.url is not None:
            page = "/decision"
        return self.url, self.error, page, self.decision_error

    def read_observation(self) -> tuple[dict[str, Any] | None, str | None]:
        try:
            self._observation = fetch_frame_sample(self.base_url, timeout_s=self.timeout_s,
                                                         cache=self._observation_cache)
            return self._observation[0], None
        except (ConnectionError, OSError, TypeError, ValueError) as exc:
            self._observation = None
            self.error = f"{type(exc).__name__}: {exc}"
            if self.observation is not None:
                self.observation.invalidate(self.error)
            return None, self.error

    def publish(self, publication: dict[str, Any] | None) -> None:
        if self.server is None or self.decision is None or self.observation is None:
            return
        if publication is None:
            return
        try:
            pair = self._observation
            if pair is None or pair[0] is not publication:
                publication, error = self.read_observation()
                if error is not None:
                    return
                pair = self._observation
            publication, jpeg = pair
            # A stale, raised, or imageless frame stays on the page.
            not_live = health_error(publication)
            if not_live is not None:
                self.observation.invalidate(not_live)
                return
            try:
                published = self.decision.publish_publication(publication, jpeg)
                self.decision_error = None if published else "the view rejected the host frame"
                publication = self.decision.publication
            except Exception as exc:  # A failed diagnostic does not hide the frame.
                self.decision_error = f"{type(exc).__name__}: {exc}"
                self.observation.publish(publication, jpeg)
            self.error = None
        except (ConnectionError, OSError, TypeError, ValueError) as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            self.observation.invalidate(self.error)

    def stop(self) -> None:
        """End this view's publication. Safe after a partial start and when called again."""

        server, self.server = self.server, None
        self.decision = self.observation = None
        if server is not None:
            server.stop()
