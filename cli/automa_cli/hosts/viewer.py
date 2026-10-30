"""``hosts viewer``: the runtime view a host's publications feed.

``ViewFeed`` serves a loopback view from the host's observation and decision
publications. ``hosts viewer`` serves one until interrupted, and the
``plugins stream`` screens feed one beside their terminal output.
"""
from __future__ import annotations

import time
from typing import Any

from ..shared.command_result import CommandResult
from ..shared.controller_releases import RuntimeHostError
from ..frontend.server import RuntimeViewServer
from ..plugins.catalog import PluginCatalogClient
from ..runtime_view import open_view_message, runtime_view_server
from .decision_live import DecisionViewAdapter
from .decision_publication import health_error, publication_to_frame_record
from .host_publications import TransactionCache, fetch_observation_sample
from .runtime_hosts import local_hosts, runtime_base_url
from .runtime_monitor import PERCEPTION_COPY_INTERVAL_S, pause_s
from .view_discovery import runtime_view_dir


def serve_host_viewer(
    *, host_name: str | None = None, host_url: str | None = None, timeout_s: float = 3.0,
) -> CommandResult:
    """Serve the runtime view fed from a local host until interrupted.

    The host is ``host_name``, or the one at ``host_url``, the host URL
    ``hosts status`` shows. The view opens only once the host answers, and
    refreshes at the cadence of a run's view. While the host is not live its
    pages say why.
    """

    if host_name is not None:
        try:
            base_url = runtime_base_url(host_name)
        except RuntimeHostError as exc:
            return CommandResult(2, str(exc))
    else:
        hosts = [host for host in local_hosts() if host["url"]]
        matched = [host for host in hosts if host["url"].rstrip("/") == host_url.rstrip("/")]
        if not matched:
            names = ", ".join(f"{host['host_name']} at {host['url']}" for host in hosts)
            return CommandResult(2, f"No host at {host_url}. " + (f"Hosts: {names}." if names else "None is running."))
        host_name, base_url = matched[0]["host_name"], matched[0]["url"]
    view = ViewFeed(host_name, base_url=base_url, timeout_s=timeout_s)
    try:
        publication, error = view.read_observation()
        if view.url is None or error is not None:
            return CommandResult(2, f"Cannot view host {host_name} at {base_url}: {view.error}")
        view.publish(publication)
        print(f"Viewing host {host_name} at {base_url}", flush=True)
        not_live = health_error(publication)
        if not_live is not None:
            print(f"Not live: {not_live}", flush=True)
        print(open_view_message(view.url), flush=True)
        while True:
            started = time.monotonic()
            view.publish(view.read_observation()[0])
            time.sleep(pause_s(started, PERCEPTION_COPY_INTERVAL_S))
    except KeyboardInterrupt:
        return CommandResult(130, "")
    finally:
        view.stop()


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

    def decision_line(self) -> str:
        """The decision page's address, or why it has no decision to show."""

        page = self.server.decision.page_url() if self.server is not None else None
        if self.server is None or self.server.url is None or page is None:
            return _view_line("action view", None, self.error or self.decision_error or "no action yet")
        line = f"action view: {self.server.url.rstrip('/')}{page}"
        return f"{line}  (latest unavailable: {self.decision_error})" if self.decision_error else line

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


def view_page_lines(view: ViewFeed | None, *pages: tuple[str, str]) -> list[str]:
    """Each ``(label, route)`` page of a stream's local view, then its action view.

    A stream without a view, as with ``--once``, shows none.
    """

    if view is None:
        return []
    return [*(_view_line(label, view.url, view.error, route) for label, route in pages), view.decision_line()]
