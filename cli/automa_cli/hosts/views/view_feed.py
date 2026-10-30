"""Feed a local runtime view from a host's publications."""

from __future__ import annotations

from typing import Any

from ...frontend.server import RuntimeViewServer
from .decision_live import DecisionViewAdapter
from ..publications.decision_publication import health_error, publication_to_frame_record
from ..publications.host_publications import TransactionCache, fetch_observation_sample
from ..publications.plugin_catalog import PluginCatalogClient
from .runtime_view import runtime_view_server
from .view_discovery import runtime_view_dir


class ViewFeed:
    """A loopback runtime view a terminal stream feeds from the host's publications.

    As the view of ``hosts run`` does, its decision page aggregates the
    host's latest decision with its matched frame, evidence and candidates, and
    it serves the host's plugin catalog. The perception and memory pages publish
    from the observation even while no matched decision is available.
    """

    def __init__(self, host_name: str, *, base_url: str, timeout_s: float) -> None:
        runtime_dir = runtime_view_dir(host_name)
        runtime_dir.mkdir(parents=True, exist_ok=True)
        self.base_url = base_url
        self.timeout_s = timeout_s
        self.frame_path = runtime_dir / "latest_frame.jpg"
        self.server: RuntimeViewServer | None = None
        self.decision: DecisionViewAdapter | None = None
        self.error: str | None = None
        self.decision_error: str | None = None
        self._observation_cache = TransactionCache()
        self._observation: tuple[dict[str, Any], bytes | None] | None = None
        self._last_capture: tuple[Any, ...] | None = None
        try:
            self.server = runtime_view_server(
                host_name=host_name,
                automation_dir=runtime_dir,
                plugin_catalog=PluginCatalogClient(base_url, timeout_s=timeout_s),
            ).start()
            self.decision = DecisionViewAdapter(
                host_name=host_name, base_url=base_url, view_server=self.server, timeout_s=timeout_s,
            )
        except OSError as exc:
            self.error = f"{type(exc).__name__}: {exc}"

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
            page = self.server.decision.page_url()
        return self.url, self.error, page, self.decision_error

    def decision_line(self) -> str:
        """The decision page's address, or why it has no decision to show."""

        _url, error, page, decision_error = self.state()
        if self.server is None or self.url is None or page is None:
            return _view_line("action view", None, error or decision_error or "no action yet")
        line = f"action view: {self.url.rstrip('/')}{page}"
        return f"{line}  (latest unavailable: {decision_error})" if decision_error else line

    def read_observation(self) -> tuple[dict[str, Any] | None, str | None]:
        try:
            self._observation = fetch_observation_sample(self.base_url, timeout_s=self.timeout_s,
                                                         cache=self._observation_cache)
            return self._observation[0], None
        except (ConnectionError, OSError, TypeError, ValueError) as exc:
            self._observation = None
            self.error = f"{type(exc).__name__}: {exc}"
            if self.server is not None:
                self.server.perception.invalidate_latest(self.error)
            return None, self.error

    def publish(self, publication: dict[str, Any] | None) -> None:
        if self.server is None or self.decision is None:
            return
        try:
            published = self.decision.refresh()
            self.decision_error = None if published else "the view rejected the host's cycle"
        except Exception as exc:  # noqa: BLE001 - the decision page is observational
            self.server.decision.invalidate_latest()
            self.decision_error = f"{type(exc).__name__}: {exc}"
        # The observation publishes last, as the latest perception and memory.
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
            # A host whose run stopped still serves its last frame, as stale.
            not_live = health_error(publication)
            if jpeg is None or not_live is not None:
                self.server.perception.invalidate_latest(not_live)
                self._last_capture = None
                return
            frame = publication_to_frame_record(publication)
            capture = (frame.get("frame_id"), frame.get("captured_at_ms"),
                       frame.get("perception_completed_at_ms"), frame.get("generation_id"))
            if capture != self._last_capture:
                self.frame_path.write_bytes(jpeg)
                self.server.perception.publish_matched(frame_path=self.frame_path, frame_record=frame)
                self._last_capture = capture
            self.error = None
        except (ConnectionError, OSError, TypeError, ValueError) as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            self.server.perception.invalidate_latest(self.error)
            self._last_capture = None

    def stop(self) -> None:
        if self.server is not None:
            self.server.stop()


def _view_line(label: str, url: str | None, error: str | None, route: str = "") -> str:
    if url:
        return f"{label}: {url.rstrip('/') + route if route else url}"
    return f"{label}: unavailable ({error})" if error else f"{label}: unavailable"
