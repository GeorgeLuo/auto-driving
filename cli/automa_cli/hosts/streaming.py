"""Live step streams and probes, read from a runtime host.

Every vehicle host serves ``autonomy.runtime.routes``, so a stream resolves
the host's ``base_url`` (``runtime_hosts``) and reads the same routes for a
PiCar or a Chase car: ``/host/status`` for the steps the host runs, and
the observation publication for the latest cycle.
"""
from __future__ import annotations

import json
import time
from typing import Any, Callable, TextIO

from autonomy.decision_cycle.memory.interface import (
    BOUNDS,
    EPOCH_ID,
    HEALTH,
    RECORD_COUNT,
)
from autonomy.decision_cycle.timing import step_timing
from autonomy.runtime.frame_loop import LATEST_FRAME_PATH, LATEST_JSON_PATH
from ..shared.command_result import CommandResult
from .decision_live import DecisionViewAdapter
from ..shared.memory_report import evidence_publisher, ledger_summary, plugin_ledgers
from .decision_publication import (
    perception_text_from_publication,
    presets_text,
    publication_to_frame_record,
)
from .host_publications import (
    fetch_autonomy_status,
    fetch_observation_sample, TransactionCache,
    fetch_observation_publication,
)
from ..plugins.catalog import PluginCatalogClient
from .run_record import timestamp_ms
from ..shared.controller_releases import RuntimeHostError
from .runtime_hosts import local_hosts, runtime_base_url
from .runtime_monitor import PERCEPTION_COPY_INTERVAL_S, pause_s
from ..frontend.server import RuntimeViewServer
from ..runtime_view import open_view_message, runtime_view_server
from .step_activations import absent_step_error
from .stream_output import write_stream_frame
from .view_discovery import runtime_view_dir

PERCEPTION_LIVE_SCHEMA = "host_perception_live"
# Every step's live probe schema; their probes and live screens are in this module.
MEMORY_LIVE_SCHEMA = "host_memory_live"
LIVE_STEP_SCHEMAS = {
    "memory": MEMORY_LIVE_SCHEMA,
    **{step: f"host_{step}_live" for step in ("proposal", "plan", "action")},
}
# Observation publication health -> probe status; "healthy" is the only live one.
_PERCEPTION_STATUS = {
    "warming": "absent",
    "absent": "absent",
    "stale": "stale",
    "unavailable": "unavailable",
    "error": "error",
}


def stream_host_perception(
    *,
    host_name: str,
    refresh_s: float = 0.5,
    once: bool = False,
    no_clear: bool = False,
    timeout_s: float = 3.0,
    json_output: bool = False,
    output: TextIO | None = None,
) -> CommandResult:
    """Poll the latest perception the host publishes.

    JSON mode prints each probe alone, including the unavailable one when no
    host can be addressed. The terminal view renders the same probe verdict
    and feeds a local runtime view from the same publication.
    """

    try:
        base_url = runtime_base_url(host_name)
    except RuntimeHostError as exc:
        return CommandResult(*stream_preflight_outcome(
            step="perception", live=unavailable_probe(PERCEPTION_LIVE_SCHEMA, host_name, str(exc)),
            message=str(exc), json_output=json_output, stream=output,
        ))
    view = None if json_output or once else ViewFeed(host_name, base_url=base_url, timeout_s=timeout_s)

    def probe() -> tuple[dict[str, Any], Any]:
        publication, fetch_error = (view.read_observation() if view is not None
                                    else _read_publication(base_url, timeout_s=timeout_s))
        live = _probe_perception(
            host_name=host_name,
            base_url=base_url,
            publication=publication,
            fetch_error=fetch_error,
        )
        return live, publication

    def render(live: dict[str, Any], publication: Any) -> str:
        if view is not None:
            view.publish(publication)
        return _perception_screen(
            host_name=host_name,
            live=live,
            base_url=base_url,
            publication=publication,
            view=view,
        )

    try:
        return poll_stream(
            step="perception",
            probe=probe,
            render=render,
            refresh_s=refresh_s,
            once=once,
            no_clear=no_clear,
            json_output=json_output,
            stream=output,
        )
    finally:
        if view is not None:
            view.stop()


def unavailable_probe(schema: str, host_name: str, error: str) -> dict[str, Any]:
    return {
        "schema": schema,
        "host_name": host_name,
        "status": "unavailable",
        "error": error,
        "probed_at_ms": timestamp_ms(),
    }


def stream_preflight_outcome(
    *,
    step: str,
    live: dict[str, Any],
    message: str,
    json_output: bool,
    stream: TextIO | None,
) -> tuple[int, str]:
    """Preserve JSON stream output when a stream cannot start polling.

    Preflight failures, such as no addressable runtime host, terminate with
    exit 2 in either mode. JSON mode emits the one non-live probe ``live``,
    including with ``--once`` omitted; terminal mode returns ``message`` for
    the CLI handler to print.
    """

    if not json_output:
        return 2, message
    line = json.dumps(live, sort_keys=True)
    if stream is not None:
        print(line, file=stream, flush=True)
    return once_stream_outcome(step=step, live=live, line=line, stream=stream)


def once_stream_outcome(
    *,
    step: str,
    live: dict[str, Any],
    line: str,
    stream: TextIO | None,
) -> tuple[int, str]:
    """Exit code and message of a one-shot step stream: 0 only when ``step`` is live.

    Non-live statuses (stopped, stale, absent, error, unavailable, …) keep their
    structured diagnostic payload/line but return nonzero so automation cannot
    treat retained or stopped state as success.
    """

    status = str(live.get("status") or "unknown")
    if status == "live":
        # Avoid double-print when the handler also emits result.message.
        return 0, "" if stream is not None else line
    diagnostic = str(live.get("error") or f"{step} stream is not live (status={status})")
    return 2, "" if stream is not None else (line or diagnostic)


def poll_stream(
    *,
    step: str,
    probe: Callable[[], tuple[dict[str, Any], Any]],
    render: Callable[[dict[str, Any], Any], str],
    refresh_s: float,
    once: bool,
    no_clear: bool,
    json_output: bool,
    stream: TextIO | None,
) -> CommandResult:
    """The polling loop behind every step stream.

    ``probe`` returns the live probe and the source material it read;
    ``render`` turns both into the terminal screen. JSON mode prints the
    probe alone. ``once`` writes one frame without clearing the terminal and
    exits 0 only when the probe is live.
    """

    try:
        while True:
            live, source = probe()
            line = json.dumps(live, sort_keys=True) if json_output else render(live, source)
            write_stream_frame(stream, line, clear=not (no_clear or once or json_output))
            if once:
                return CommandResult(
                    *once_stream_outcome(step=step, live=live, line=line, stream=stream)
                )
            time.sleep(max(0.1, float(refresh_s)))
    except KeyboardInterrupt:
        return CommandResult(130, "")


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
        not_live = _health_error(publication)
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
            not_live = _health_error(publication)
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


def _health_error(publication: dict[str, Any]) -> str | None:
    """Why the host's observation ``publication`` is not live; None while it is healthy."""

    health = str(publication.get("health") or "unknown")
    if health == "healthy":
        return None
    return publication.get("error") or f"The observation publication health is {health!r}."


def _read_publication(base_url: str, *, timeout_s: float) -> tuple[dict[str, Any] | None, str | None]:
    try:
        return fetch_observation_publication(base_url, timeout_s=timeout_s), None
    except ConnectionError as exc:
        return None, str(exc)


def _probe_perception(
    *,
    host_name: str,
    base_url: str,
    publication: dict[str, Any] | None,
    fetch_error: str | None,
) -> dict[str, Any]:
    probe: dict[str, Any] = {
        "schema": PERCEPTION_LIVE_SCHEMA,
        "host_name": host_name,
        "endpoint": f"{base_url}{LATEST_JSON_PATH}",
        "probed_at_ms": timestamp_ms(),
    }
    if fetch_error is not None or publication is None:
        return {**probe, "status": "error", "error": fetch_error or "No publication was read."}

    health = str(publication.get("health") or "unknown")
    probe.update(
        health=health,
        presets=publication.get("presets"),
        mode=publication.get("mode"),
        frames_received=publication.get("frames_received"),
        frames_dropped=publication.get("frames_dropped"),
        processed_count=publication.get("processed_count"),
    )
    error = _health_error(publication)
    if error is not None:
        return {**probe, "status": _PERCEPTION_STATUS.get(health, "error"), "error": error}
    # The host computes the result age on its own clock.
    return _probe_latest_perception(
        probe,
        publication_to_frame_record(publication),
        age_ms=publication.get("result_age_ms"),
    )


def _probe_latest_perception(
    probe: dict[str, Any],
    record: dict[str, Any],
    *,
    age_ms: Any,
) -> dict[str, Any]:
    """The latest frame's perception, as every provider's live probe reports it."""

    frame = {
        "frame_id": record.get("frame_id"),
        "frame_index": record.get("frame_index"),
        "captured_at_ms": record.get("captured_at_ms"),
        "perception_completed_at_ms": record.get("perception_completed_at_ms"),
        "perception_duration_ms": record.get("perception_duration_ms"),
        "age_ms": age_ms,
    }
    perception = record.get("perception")
    if not isinstance(perception, dict):
        return {
            **probe,
            **frame,
            "status": "absent",
            "error": f"Latest frame {record.get('frame_id')!r} has no perception result.",
        }
    signals = perception.get("signals")
    things = perception.get("things")
    return {
        **probe,
        **frame,
        "status": "live",
        "signal_count": len(signals) if isinstance(signals, list) else None,
        "thing_count": len(things) if isinstance(things, list) else None,
        "perception": perception,
    }


def _perception_screen(
    *,
    host_name: str,
    live: dict[str, Any],
    base_url: str,
    publication: dict[str, Any] | None,
    view: ViewFeed | None,
) -> str:
    """The perception screen: the probe verdict and the host's latest cycle."""

    publication = publication if isinstance(publication, dict) else {}
    record = publication_to_frame_record(publication)
    perception = record.get("perception") if isinstance(record.get("perception"), dict) else {}
    control = record.get("control") if isinstance(record.get("control"), dict) else {}
    signals = perception.get("signals")
    things = perception.get("things")
    cadence = {
        "processed": publication.get("processed_count"),
        "cycle_ms": publication.get("duration_ms"),
        # The host computes the result age on its own clock.
        "age_ms": publication.get("result_age_ms"),
    }
    body = perception_text_from_publication(publication) if publication else ""
    lines = [
        "automa perception stream",
        "",
        f"host: {host_name}",
        f"host: {base_url}",
        f"status: {live.get('status', 'unknown')}",
        *([f"error: {live['error']}"] if live.get("error") else []),
        f"presets: {presets_text(publication.get('presets'))}  mode: {_shown(publication.get('mode'))}",
        (
            f"control: steering={_shown(control.get('steering'))}  "
            f"throttle={_shown(control.get('throttle'))}  reason={_shown(control.get('reason'))}"
        ),
        "cadence: " + "  ".join(f"{key}={_shown(value)}" for key, value in cadence.items()),
        (
            f"latest: frame={_shown(record.get('frame_id'), 'none')}  "
            f"captured_at_ms={_shown(record.get('captured_at_ms'))}  "
            f"signals={_shown(len(signals) if isinstance(signals, list) else None)}  "
            f"things={_shown(len(things) if isinstance(things, list) else None)}"
        ),
        *view_page_lines(view, ("view", "")),
        (
            f"publication: {publication.get('health') or 'unavailable'}  "
            f"json: {LATEST_JSON_PATH}  frame: {LATEST_FRAME_PATH}"
        ),
        "",
        "latest perception",
        "-----------------",
        body.strip() or "(no latest perception yet)",
    ]
    return "\n".join(lines)


def _shown(value: Any, default: str = "unknown") -> Any:
    return default if value is None else value


def stream_host_memory(
    *,
    host_name: str,
    refresh_s: float = 0.5,
    once: bool = False,
    no_clear: bool = False,
    timeout_s: float = 3.0,
    json_output: bool = False,
    output: TextIO | None = None,
) -> CommandResult:
    """Poll the live memory step the host runs.

    ``live`` means the host runs a memory step; update health stays in the
    plugin diagnostics. Each probe lists every applied plugin's ledger and
    names the evidence publisher; the terminal view prints the same, then the
    memory map and perception view URLs and the latest published memory
    report.
    """

    try:
        base_url = runtime_base_url(host_name)
    except RuntimeHostError as exc:
        return CommandResult(*stream_preflight_outcome(
            step="memory", live=unavailable_probe(MEMORY_LIVE_SCHEMA, host_name, str(exc)),
            message=str(exc), json_output=json_output, stream=output,
        ))
    view = None if json_output or once else ViewFeed(host_name, base_url=base_url, timeout_s=timeout_s)

    def render(live: dict[str, Any], _source: Any) -> str:
        publication, fetch_error = (view.read_observation() if view is not None
                                    else _read_publication(base_url, timeout_s=timeout_s))
        if view is not None:
            view.publish(publication)
        return _memory_screen(
            host_name=host_name,
            live=live,
            view_lines=view_page_lines(view, ("memory map", "/memory"), ("perception view", "/perception")),
            report=publication.get("memory") if isinstance(publication, dict) else None,
            report_error=fetch_error,
        )

    try:
        return poll_stream(
            step="memory",
            probe=lambda: (
                _probe_step("memory", host_name=host_name, base_url=base_url, timeout_s=timeout_s),
                None,
            ),
            render=render,
            refresh_s=refresh_s,
            once=once,
            no_clear=no_clear,
            json_output=json_output,
            stream=output,
        )
    finally:
        if view is not None:
            view.stop()


def _memory_screen(
    *,
    host_name: str,
    live: dict[str, Any],
    view_lines: list[str],
    report: Any,
    report_error: str | None,
) -> str:
    """The memory screen: the host's memory step, the views, and the memory
    report its latest cycle published."""

    lines = [
        format_live_memory_screen(host_name=host_name, live=live),
        "",
        *view_lines,
    ]
    if report_error:
        lines.append(f"publication memory: {report_error}")
    elif isinstance(report, dict):
        lines.append(
            f"publication memory: evidence publisher {evidence_publisher(report) or 'none'}"
        )
        lines.extend(_format_plugin_ledgers(plugin_ledgers(report)))
    return "\n".join(lines)


def probe_live_memory(*, host_name: str, timeout_s: float = 3.0) -> dict[str, Any]:
    """Return a normalized live-memory probe without requiring stream mode."""

    return probe_live_step("memory", host_name=host_name, timeout_s=timeout_s)


def probe_live_step(step: str, *, host_name: str, timeout_s: float = 3.0) -> dict[str, Any]:
    """``step`` as the host runs it: its runner's status
    under ``/host/status``, not what any view last rendered."""

    try:
        base_url = runtime_base_url(host_name)
    except RuntimeHostError as exc:
        return unavailable_probe(LIVE_STEP_SCHEMAS[step], host_name, str(exc))
    return _probe_step(step, host_name=host_name, base_url=base_url, timeout_s=timeout_s)


def _probe_step(step: str, *, host_name: str, base_url: str, timeout_s: float) -> dict[str, Any]:
    probe: dict[str, Any] = {
        "schema": LIVE_STEP_SCHEMAS[step],
        "host_name": host_name,
        "endpoint": f"{base_url}/host/status",
        "probed_at_ms": timestamp_ms(),
    }
    try:
        status = fetch_autonomy_status(base_url, timeout_s=timeout_s)
    except ConnectionError as exc:
        return {**probe, "status": "error", "error": str(exc)}

    session = status.get("session") if isinstance(status.get("session"), dict) else {}
    probe.update(mode=status.get("mode"), session_status=session.get("status"))
    autonomy = status.get("host") if isinstance(status.get("host"), dict) else {}
    steps = autonomy.get("steps") if isinstance(autonomy.get("steps"), dict) else {}
    runner = steps.get(step) if isinstance(steps.get(step), dict) else None
    if runner is None:
        return {**probe, "status": "absent", "error": absent_step_error(step, host_name)}
    fields = _live_step_fields(step, runner)
    # The host's cycle times each step call; the runner keeps only its
    # plugins' times.
    last_cycle = autonomy.get("last_cycle") if isinstance(autonomy.get("last_cycle"), dict) else {}
    entry = step_timing(last_cycle.get("timing"), step)
    fields["last_duration_ms"] = entry.get("duration_ms") if entry is not None else None
    return {**probe, "status": "live", **fields}


def _live_step_fields(step: str, status: dict[str, Any]) -> dict[str, Any]:
    """The runner status fields a live probe reports; memory adds its ledgers."""

    fields = {
        "activation": status.get("activation"),
        "plugin_ids": status.get("plugin_ids", []),
        "selected_plugin_ids": status.get("selected_plugin_ids", []),
        "plugin_report": status.get("plugin_report"),
        "last_error": status.get("last_error"),
        "failure_count": status.get("failure_count"),
    }
    if step == "memory":
        fields.update(
            plugins=_probe_plugins(status),
            evidence_publisher=evidence_publisher(status),
            update_count=status.get("update_count"),
            reset_count=status.get("reset_count"),
        )
    else:
        fields["run_count"] = status.get("run_count")
    return fields


def _probe_plugins(status: dict[str, Any]) -> list[dict[str, Any]]:
    """Each applied plugin's step status with its ledger summary beside it.

    An entry keeps the step's per-plugin status (counters, ``last_error``,
    ``state``) and adds ``epoch_id``, ``health``, ``bounds`` and
    ``record_count`` from that plugin's state, None where the plugin reports
    none.
    """

    plugins = status.get("plugins")
    return [
        {
            **entry,
            **ledger_summary(entry.get("state") if isinstance(entry.get("state"), dict) else None),
        }
        for entry in (plugins if isinstance(plugins, list) else [])
        if isinstance(entry, dict) and isinstance(entry.get("plugin_id"), str)
    ]


def _live_plugins(live: dict[str, Any]) -> list[dict[str, Any]]:
    """The plugin entries of a live probe; none unless it is live."""

    plugins = live.get("plugins") if live.get("status") == "live" else None
    return [
        entry
        for entry in (plugins if isinstance(plugins, list) else [])
        if isinstance(entry, dict) and isinstance(entry.get("plugin_id"), str)
    ]


def format_live_memory_screen(*, host_name: str, live: dict[str, Any]) -> str:
    return format_live_step_screen("memory", host_name=host_name, live=live)


def format_live_step_screen(step: str, *, host_name: str, live: dict[str, Any]) -> str:
    status = str(live.get("status") or "unknown")
    lines = [
        f"Live {step}: {host_name} [{status}]",
    ]
    if live.get("endpoint"):
        lines.append(f"Endpoint: {live.get('endpoint')}")
    if live.get("mode") is not None:
        lines.append(f"Mode: {live.get('mode')}  session: {live.get('session_status') or 'unknown'}")
    if status == "live":
        lines.append(f"Applied plugins: {', '.join(live.get('plugin_ids', [])) or 'none'}")
        if step == "memory":
            lines.extend(_format_plugin_ledgers(_live_plugins(live)))
            lines.extend(
                [
                    f"Evidence publisher: {live.get('evidence_publisher') or 'none'}",
                    (
                        f"Counters: updates={live.get('update_count')} "
                        f"resets={live.get('reset_count')} "
                        f"failures={live.get('failure_count')}"
                    ),
                ]
            )
        else:
            lines.append(
                f"Counters: runs={live.get('run_count')} failures={live.get('failure_count')}"
            )
        if live.get("last_duration_ms") is not None:
            lines.append(f"Last duration: {live.get('last_duration_ms')} ms")
        if live.get("last_error"):
            lines.append(f"Last error: {live.get('last_error')}")
    else:
        if live.get("error"):
            lines.append(f"Detail: {live.get('error')}")
    return "\n".join(lines)


def _format_plugin_ledgers(ledgers: list[dict[str, Any]]) -> list[str]:
    """One line per plugin: its ledger health, epoch, records and any bounds."""

    lines = []
    for ledger in ledgers:
        line = (
            f"  {ledger['plugin_id']}: health={ledger.get(HEALTH) or 'unknown'} "
            f"epoch={ledger.get(EPOCH_ID) or '-'} records={ledger.get(RECORD_COUNT)}"
        )
        bounds = ledger.get(BOUNDS)
        if isinstance(bounds, dict) and bounds:
            line += (
                f" max_records={bounds.get('max_records')} "
                f"max_age_ms={bounds.get('max_age_ms')} "
                f"eviction={bounds.get('eviction_policy')}"
            )
        lines.append(line)
    return lines
