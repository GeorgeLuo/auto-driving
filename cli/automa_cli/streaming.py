from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from .automation import (
    CHASE_WORKER_PROBE_MAX_AGE_MS,
    _automation_dir,
    assess_chase_worker_liveness,
)
from .paths import display_path
from .runtime_view import RuntimeViewServer
from autonomy.decision_cycle.memory.interface import (
    BOUNDS,
    EPOCH_ID,
    HEALTH,
    RECORD_COUNT,
)
from .memory_report import evidence_publisher, ledger_summary, plugin_ledgers
from .physical_observation import (
    LATEST_FRAME_PATH,
    LATEST_JSON_PATH,
    fetch_autonomy_status,
    fetch_observation_frame,
    fetch_observation_publication,
    perception_text_from_publication,
    physical_observation_dir,
    picar_base_url,
    publication_to_frame_record,
)
from .vehicles import discover_active_vehicles, find_vehicle_by_id, format_active_vehicles

PERCEPTION_LIVE_SCHEMA = "vehicle_perception_live_v0"
# Memory's and proposal's live schemas sit with perception's. Their probe and
# live screen, and memory's stream, are in this module.
MEMORY_LIVE_SCHEMA = "vehicle_memory_live_v1"
PROPOSAL_LIVE_SCHEMA = "vehicle_proposal_live_v1"
LIVE_STEP_SCHEMAS = {"memory": MEMORY_LIVE_SCHEMA, "proposal": PROPOSAL_LIVE_SCHEMA}
# Onboard publication health -> probe status; "healthy" is the only live one.
_PHYSICAL_PERCEPTION_STATUS = {
    "warming": "absent",
    "absent": "absent",
    "stale": "stale",
    "unavailable": "unavailable",
    "error": "error",
}


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    message: str


def stream_vehicle_perception(
    *,
    vehicle_id: str,
    refresh_s: float = 0.5,
    once: bool = False,
    no_clear: bool = False,
    timeout_s: float = 3.0,
    json_output: bool = False,
    output: TextIO | None = None,
) -> CommandResult:
    """Poll the latest perception for Chase or PiCar.

    JSON mode emits probes even when discovery fails. Terminal mode renders
    the same probe verdict used by ``--once``; worker state and publication
    health remain separate diagnostics.
    """

    payload = discover_active_vehicles(
        timeout_s=timeout_s,
        include_picar=True,
        include_chase_sim=True,
        include_inactive=True,
    )
    vehicle, error = find_vehicle_by_id(payload, vehicle_id)
    if error:
        return CommandResult(
            *unavailable_stream_outcome(
                step="perception",
                schema=PERCEPTION_LIVE_SCHEMA,
                vehicle_id=vehicle_id,
                message="\n\n".join(
                    [
                        error,
                        "Discovery:",
                        format_active_vehicles(payload, include_inactive=True),
                    ]
                ),
                json_output=json_output,
                stream=output,
            )
        )
    if vehicle is None:
        return CommandResult(
            *unavailable_stream_outcome(
                step="perception",
                schema=PERCEPTION_LIVE_SCHEMA,
                vehicle_id=vehicle_id,
                message=f"Vehicle {vehicle_id!r} was not found.",
                json_output=json_output,
                stream=output,
            )
        )

    provider = vehicle.get("provider")
    if provider == "chase-sim":
        return _stream_chase_perception(
            vehicle_id=vehicle_id,
            refresh_s=refresh_s,
            once=once,
            no_clear=no_clear,
            json_output=json_output,
            output=output,
        )
    if provider == "picar":
        return _stream_physical_perception(
            vehicle_id=vehicle_id,
            vehicle=vehicle,
            refresh_s=refresh_s,
            once=once,
            no_clear=no_clear,
            timeout_s=timeout_s,
            json_output=json_output,
            output=output,
        )
    return CommandResult(
        *unavailable_stream_outcome(
            step="perception",
            schema=PERCEPTION_LIVE_SCHEMA,
            vehicle_id=vehicle_id,
            message=f"Vehicle {vehicle_id!r} is provider {provider!r}; perception stream supports chase-sim and picar.",
            json_output=json_output,
            stream=output,
        )
    )


def probe_live_perception(
    *,
    vehicle_id: str,
    vehicle: dict[str, Any] | None = None,
    timeout_s: float = 3.0,
) -> dict[str, Any]:
    """Return a normalized live-perception probe without requiring stream mode."""

    if vehicle is None:
        discovery = discover_active_vehicles(
            timeout_s=timeout_s,
            include_picar=True,
            include_chase_sim=True,
            include_inactive=True,
        )
        vehicle, error = find_vehicle_by_id(discovery, vehicle_id)
        if error or vehicle is None:
            return {
                "schema": PERCEPTION_LIVE_SCHEMA,
                "vehicle_id": vehicle_id,
                "status": "unavailable",
                "error": error or f"Vehicle {vehicle_id!r} was not found.",
                "probed_at_ms": _timestamp_ms(),
            }

    provider = vehicle.get("provider")
    if provider == "picar":
        base_url = picar_base_url(vehicle)
        publication, fetch_error = _read_physical_publication(base_url, timeout_s=timeout_s)
        return _probe_physical_perception(
            vehicle_id=vehicle_id,
            base_url=base_url,
            publication=publication,
            fetch_error=fetch_error,
        )
    if provider == "chase-sim":
        return _probe_chase_perception(vehicle_id=vehicle_id)
    return {
        "schema": PERCEPTION_LIVE_SCHEMA,
        "vehicle_id": vehicle_id,
        "status": "unavailable",
        "error": (
            f"Vehicle {vehicle_id!r} is provider {provider!r}; "
            "live perception supports picar and chase-sim."
        ),
        "probed_at_ms": _timestamp_ms(),
    }


def unavailable_stream_outcome(
    *,
    step: str,
    schema: str,
    vehicle_id: str,
    message: str,
    json_output: bool,
    stream: TextIO | None,
) -> tuple[int, str]:
    """Preserve JSON stream output when discovery or provider selection fails.

    Preflight failures terminate with exit 2 in either mode. JSON mode emits
    one unavailable probe under the step's live probe ``schema``, including
    with ``--once`` omitted; terminal mode returns the discovery diagnostic for
    the CLI handler to print.
    """

    if not json_output:
        return 2, message
    live = {
        "schema": schema,
        "vehicle_id": vehicle_id,
        "status": "unavailable",
        "error": message,
        "probed_at_ms": _timestamp_ms(),
    }
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


def _stream_chase_perception(
    *,
    vehicle_id: str,
    refresh_s: float,
    once: bool,
    no_clear: bool,
    json_output: bool,
    output: TextIO | None,
) -> CommandResult:
    stream = output
    automation_dir = _automation_dir(vehicle_id)
    state_path = automation_dir / "state.json"
    process_path = automation_dir / "process.json"
    latest_text_path = automation_dir / "latest_perception.txt"
    latest_json_path = automation_dir / "latest_perception.json"
    default_log_path = automation_dir / "automation.log"

    if not json_output and not automation_dir.exists():
        return CommandResult(
            2,
            "\n".join(
                [
                    f"No automation runtime exists for {vehicle_id!r}.",
                    f"Expected: {display_path(automation_dir)}",
                    f"Run: ./cli/automa vehicles automation run --id {vehicle_id}",
                ]
            ),
        )

    try:
        while True:
            live = _probe_chase_perception(vehicle_id=vehicle_id)
            if json_output:
                line = json.dumps(live, sort_keys=True)
            else:
                line = _render_chase_perception_screen(
                    vehicle_id=vehicle_id,
                    live=live,
                    state=_read_json(state_path),
                    process=_read_json(process_path),
                    latest=_read_json(latest_json_path),
                    latest_text=_read_text(latest_text_path),
                    state_path=state_path,
                    default_log_path=default_log_path,
                )
            if stream is not None:
                if not no_clear and not json_output:
                    print("\033[2J\033[H", end="", file=stream)
                print(line, file=stream, flush=True)

            if once:
                return CommandResult(
                    *once_stream_outcome(step="perception", live=live, line=line, stream=stream)
                )
            time.sleep(max(0.1, float(refresh_s)))
    except KeyboardInterrupt:
        return CommandResult(130, "")


def _stream_physical_perception(
    *,
    vehicle_id: str,
    vehicle: dict[str, Any],
    refresh_s: float,
    once: bool,
    no_clear: bool,
    timeout_s: float,
    json_output: bool,
    output: TextIO | None,
) -> CommandResult:
    stream = output
    base_url = picar_base_url(vehicle)
    if not json_output and not base_url:
        return CommandResult(2, f"Vehicle {vehicle_id!r} has no picar base_url connection.")

    # The terminal view comes with the local view and its frame file; JSON
    # output prints the probe alone.
    view_server: RuntimeViewServer | None = None
    view_error: str | None = None
    frame_path: Path | None = None
    if not json_output:
        runtime_dir = physical_observation_dir(vehicle_id)
        runtime_dir.mkdir(parents=True, exist_ok=True)
        frame_path = runtime_dir / "latest_frame.jpg"
        try:
            view_server = RuntimeViewServer(
                vehicle_id=vehicle_id,
                automation_dir=runtime_dir,
            ).start()
        except OSError as exc:
            view_error = f"{type(exc).__name__}: {exc}"

    try:
        while True:
            publication, fetch_error = _read_physical_publication(base_url, timeout_s=timeout_s)
            live = _probe_physical_perception(
                vehicle_id=vehicle_id,
                base_url=base_url,
                publication=publication,
                fetch_error=fetch_error,
            )

            if json_output:
                line = json.dumps(live, sort_keys=True)
            else:
                view_url = view_server.url if view_server is not None else None
                if publication is not None and view_server is not None and frame_path is not None:
                    try:
                        _publish_physical_view(
                            view_server=view_server,
                            base_url=base_url,
                            publication=publication,
                            frame_path=frame_path,
                            timeout_s=timeout_s,
                        )
                        view_url = view_server.url
                        view_error = None
                    except (ConnectionError, OSError, TypeError, ValueError) as exc:
                        view_error = f"{type(exc).__name__}: {exc}"
                line = _render_physical_perception_screen(
                    vehicle_id=vehicle_id,
                    live=live,
                    base_url=base_url,
                    publication=publication,
                    fetch_error=fetch_error,
                    view_url=view_url,
                    view_error=view_error,
                )
            if stream is not None:
                if not no_clear and not json_output:
                    print("\033[2J\033[H", end="", file=stream)
                print(line, file=stream, flush=True)

            if once:
                return CommandResult(
                    *once_stream_outcome(step="perception", live=live, line=line, stream=stream)
                )
            time.sleep(max(0.1, float(refresh_s)))
    except KeyboardInterrupt:
        return CommandResult(130, "")
    finally:
        if view_server is not None:
            view_server.stop()


def _probe_chase_perception(*, vehicle_id: str) -> dict[str, Any]:
    probed_at_ms = _timestamp_ms()
    automation_dir = _automation_dir(vehicle_id)
    state_path = automation_dir / "state.json"
    probe: dict[str, Any] = {
        "schema": PERCEPTION_LIVE_SCHEMA,
        "vehicle_id": vehicle_id,
        "provider": "chase-sim",
        "probed_at_ms": probed_at_ms,
    }
    if not state_path.exists():
        return {
            **probe,
            "status": "unavailable",
            "error": (
                f"No automation runtime state for {vehicle_id!r}. "
                f"Run: ./cli/automa vehicles automation run --id {vehicle_id}"
            ),
        }
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {**probe, "status": "error", "error": f"Could not read automation state: {exc}"}
    if not isinstance(state, dict):
        return {**probe, "status": "error", "error": "Automation state is not a JSON object."}

    liveness = assess_chase_worker_liveness(
        state=state,
        probed_at_ms=probed_at_ms,
        step="perception",
        vehicle_id=vehicle_id,
    )
    probe.update(
        worker_status=state.get("status"),
        worker_pid=liveness.get("pid"),
        worker_updated_at_ms=liveness.get("updated_at_ms"),
        run_id=state.get("run_id"),
        frames_processed=state.get("frames_processed"),
    )
    if not liveness["live"]:
        return {
            **probe,
            "status": liveness["status"],
            "error": liveness["error"],
            "max_age_ms": CHASE_WORKER_PROBE_MAX_AGE_MS,
        }

    step = state.get("perception") if isinstance(state.get("perception"), dict) else {}
    probe.update(
        activation=step.get("activation"),
        preset=step.get("preset"),
        plugin_ids=step.get("plugins", []),
    )
    record = _read_json(automation_dir / "latest_perception.json")
    # A record from an earlier run, or the start placeholder, is not this worker's.
    if record is None or record.get("run_id") != state.get("run_id"):
        return {
            **probe,
            "status": "absent",
            "error": "Automation worker has not published a perception result for this run yet.",
        }
    completed_at = _int_or_none(record.get("perception_completed_at_ms"))
    return _probe_latest_perception(
        {**probe, "plugin_report": record.get("perception_plugin_report")},
        record,
        age_ms=None if completed_at is None else max(0, probed_at_ms - completed_at),
    )


def _read_physical_publication(
    base_url: str | None,
    *,
    timeout_s: float,
) -> tuple[dict[str, Any] | None, str | None]:
    if not base_url:
        return None, None
    try:
        return fetch_observation_publication(base_url, timeout_s=timeout_s), None
    except ConnectionError as exc:
        return None, str(exc)


def _probe_physical_perception(
    *,
    vehicle_id: str,
    base_url: str | None,
    publication: dict[str, Any] | None,
    fetch_error: str | None,
) -> dict[str, Any]:
    probe: dict[str, Any] = {
        "schema": PERCEPTION_LIVE_SCHEMA,
        "vehicle_id": vehicle_id,
        "provider": "picar",
        "probed_at_ms": _timestamp_ms(),
    }
    if not base_url:
        return {
            **probe,
            "status": "unavailable",
            "error": f"Vehicle {vehicle_id!r} has no picar base_url connection.",
        }
    probe["endpoint"] = f"{base_url}{LATEST_JSON_PATH}"
    if fetch_error is not None or publication is None:
        return {**probe, "status": "error", "error": fetch_error or "No publication was read."}

    health = str(publication.get("health") or "unknown")
    probe.update(
        health=health,
        preset=publication.get("preset"),
        drive_mode=publication.get("mode") or publication.get("drive_mode"),
        min_interval_s=publication.get("min_interval_s"),
        processed_count=publication.get("processed_count"),
        skipped_count=publication.get("skipped_count"),
    )
    if health != "healthy":
        return {
            **probe,
            "status": _PHYSICAL_PERCEPTION_STATUS.get(health, "error"),
            "error": publication.get("error")
            or f"Onboard perception publication health is {health!r}.",
        }
    # The Pi computes the result age on its own clock.
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


def _publish_physical_view(
    *,
    view_server: RuntimeViewServer,
    base_url: str,
    publication: dict[str, Any],
    frame_path: Path,
    timeout_s: float,
) -> None:
    frame = publication.get("frame") if isinstance(publication.get("frame"), dict) else None
    if frame is None or not frame.get("has_image"):
        return
    jpeg, _headers = fetch_observation_frame(base_url, timeout_s=timeout_s)
    frame_path.write_bytes(jpeg)
    frame_record = publication_to_frame_record(publication)
    view_server.perception.publish_frame(frame_path=frame_path, frame_record=frame_record)
    view_server.perception.publish_perception(frame_record=frame_record)


def _render_chase_perception_screen(
    *,
    vehicle_id: str,
    live: dict[str, Any],
    state: dict[str, Any] | None,
    process: dict[str, Any] | None,
    latest: dict[str, Any] | None,
    latest_text: str,
    state_path: Path,
    default_log_path: Path,
) -> str:
    now_ms = _timestamp_ms()
    state = state if isinstance(state, dict) else {}
    process = process if isinstance(process, dict) else {}
    latest = latest if isinstance(latest, dict) else {}
    last_frame = state.get("last_frame") if isinstance(state.get("last_frame"), dict) else {}
    pid = live.get("worker_pid")

    perception = latest.get("perception") if isinstance(latest.get("perception"), dict) else {}
    things = perception.get("things")
    thing_count = len(things) if isinstance(things, list) else last_frame.get("things")
    signals = perception.get("signals")
    signal_count = len(signals) if isinstance(signals, list) else last_frame.get("signals")
    completed_at = _int_or_none(last_frame.get("perception_completed_at_ms"))
    age_ms = None if completed_at is None else max(0, now_ms - completed_at)

    header = [
        "automa perception stream",
        "",
        f"vehicle: {vehicle_id}",
        f"source: chase-sim automation worker",
        f"status: {live.get('status', 'unknown')}",
        f"worker: {live.get('worker_status') or 'unknown'}  pid: {pid or 'unknown'}",
        f"control: {state.get('control_source', 'unknown')}  action: {state.get('action_policy', 'unknown')}",
        f"recording: {state.get('recording', 'unknown')}  frames_processed: {state.get('frames_processed', 0)}",
        _chase_cadence_line(state, last_frame, age_ms),
        _latest_line(last_frame, signal_count, thing_count),
        f"state: {display_path(state_path)}",
        _log_line(process, default_log_path),
        "",
        "latest perception",
        "-----------------",
    ]
    if live.get("error"):
        header.insert(5, f"error: {live['error']}")
    body = latest_text.strip() if latest_text.strip() else "(no latest perception yet)"
    return "\n".join([*header, body])


def _render_physical_perception_screen(
    *,
    vehicle_id: str,
    live: dict[str, Any],
    base_url: str,
    publication: dict[str, Any] | None,
    fetch_error: str | None,
    view_url: str | None,
    view_error: str | None,
) -> str:
    if fetch_error is not None:
        body = ""
        health = "unavailable"
        frame_id = "none"
        age_ms: Any = "unknown"
        thing_count: Any = "unknown"
        signal_count: Any = "unknown"
        preset = "unknown"
        control_text = "unknown"
        duration_ms: Any = "unknown"
        processed: Any = "unknown"
        skipped: Any = "unknown"
        mode = "unknown"
        min_interval: Any = "unknown"
    else:
        publication = publication if isinstance(publication, dict) else {}
        health = str(publication.get("health") or "unknown")
        frame = publication.get("frame") if isinstance(publication.get("frame"), dict) else {}
        frame_id = frame.get("frame_id") or "none"
        age_ms = publication.get("result_age_ms")
        if age_ms is None:
            age_ms = "unknown"
        perception = (
            publication.get("perception") if isinstance(publication.get("perception"), dict) else {}
        )
        things = perception.get("things")
        thing_count = len(things) if isinstance(things, list) else "unknown"
        signals = perception.get("signals")
        signal_count = len(signals) if isinstance(signals, list) else "unknown"
        preset = publication.get("preset") or "unknown"
        control = publication.get("control") if isinstance(publication.get("control"), dict) else {}
        control_text = (
            f"steering={control.get('steering', 'unknown')} "
            f"throttle={control.get('throttle', 'unknown')} "
            f"reason={control.get('reason', 'unknown')}"
        )
        duration_ms = publication.get("duration_ms")
        if duration_ms is None:
            duration_ms = "unknown"
        processed = publication.get("processed_count")
        if processed is None:
            processed = "unknown"
        skipped = publication.get("skipped_count")
        if skipped is None:
            skipped = "unknown"
        mode = publication.get("mode") or publication.get("drive_mode") or "unknown"
        min_interval = publication.get("min_interval_s")
        if min_interval is None:
            min_interval = "unknown"
        body = perception_text_from_publication(publication)

    if view_url:
        view_line = f"view: {view_url}"
    elif view_error:
        view_line = f"view: unavailable ({view_error})"
    else:
        view_line = "view: unavailable"

    header = [
        "automa perception stream",
        "",
        f"vehicle: {vehicle_id}",
        f"source: physical onboard  endpoint: {base_url}",
        f"status: {live.get('status', 'unknown')}",
        f"publication: {health}  drive_mode: {mode}  preset: {preset}",
        f"control: {control_text}",
        (
            f"cadence: min_interval_s={min_interval}  processed={processed}  "
            f"skipped={skipped}  duration_ms={duration_ms}  age_ms={age_ms}"
        ),
        (
            f"latest: frame={frame_id}  signals={signal_count}  things={thing_count}  "
            f"json={LATEST_JSON_PATH}  frame={LATEST_FRAME_PATH}"
        ),
        view_line,
        "",
        "latest perception",
        "-----------------",
    ]
    if live.get("error"):
        header.insert(5, f"error: {live['error']}")
    return "\n".join([*header, body if body.strip() else "(no latest perception yet)"])


def _chase_cadence_line(
    state: dict[str, Any],
    last_frame: dict[str, Any],
    age_ms: int | None,
) -> str:
    interval = state.get("interval_s")
    perception_ms = last_frame.get("perception_duration_ms")
    cycle_ms = last_frame.get("cycle_duration_ms")
    max_frames = state.get("max_frames")
    parts = [
        f"cadence: interval_s={interval if interval is not None else 'unknown'}",
        f"max_frames={max_frames if max_frames is not None else 'unbounded'}",
        f"perception_ms={perception_ms if perception_ms is not None else 'unknown'}",
        f"cycle_ms={cycle_ms if cycle_ms is not None else 'unknown'}",
        f"age_ms={age_ms if age_ms is not None else 'unknown'}",
    ]
    return "  ".join(parts)


def _latest_line(
    last_frame: dict[str, Any],
    signal_count: Any,
    thing_count: Any,
) -> str:
    return (
        f"latest: frame={last_frame.get('frame_id', 'none')}  "
        f"captured_at_ms={last_frame.get('captured_at_ms', 'unknown')}  "
        f"signals={signal_count if signal_count is not None else 'unknown'}  "
        f"things={thing_count if thing_count is not None else 'unknown'}"
    )


def _log_line(process: dict[str, Any], default_log_path: Path) -> str:
    configured_path = process.get("log_path")
    log_to_disk = bool(process.get("log_to_disk")) or isinstance(configured_path, str)
    if not log_to_disk:
        return "log: disabled"
    if isinstance(configured_path, str) and configured_path:
        return f"log: {configured_path}"
    return f"log: {display_path(default_log_path)}"


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _timestamp_ms() -> int:
    return int(time.time() * 1000)

def stream_vehicle_memory(
    *,
    vehicle_id: str,
    refresh_s: float = 0.5,
    once: bool = False,
    no_clear: bool = False,
    timeout_s: float = 3.0,
    json_output: bool = False,
    output: TextIO | None = None,
) -> CommandResult:
    """Poll live memory lifecycle health for Chase or PiCar.

    JSON mode emits probes even when discovery fails. ``live`` means the
    retained step is available; update health stays in the plugin diagnostics.
    Each probe lists every applied plugin's ledger and names the evidence
    publisher; the terminal view prints the same.
    """

    discovery = discover_active_vehicles(
        timeout_s=timeout_s,
        include_picar=True,
        include_chase_sim=True,
        include_inactive=True,
    )
    vehicle, error = find_vehicle_by_id(discovery, vehicle_id)
    if error:
        return CommandResult(
            *unavailable_stream_outcome(
                step="memory",
                schema=MEMORY_LIVE_SCHEMA,
                vehicle_id=vehicle_id,
                message="\n\n".join(
                    [
                        error,
                        "Discovery:",
                        format_active_vehicles(discovery, include_inactive=True),
                    ]
                ),
                json_output=json_output,
                stream=output,
            )
        )
    if vehicle is None:
        return CommandResult(
            *unavailable_stream_outcome(
                step="memory",
                schema=MEMORY_LIVE_SCHEMA,
                vehicle_id=vehicle_id,
                message=f"Vehicle {vehicle_id!r} was not found.",
                json_output=json_output,
                stream=output,
            )
        )

    if vehicle.get("provider") == "picar" and not json_output:
        return _stream_physical_memory_with_inspector(
            vehicle_id=vehicle_id,
            vehicle=vehicle,
            refresh_s=refresh_s,
            once=once,
            no_clear=no_clear,
            timeout_s=timeout_s,
            output=output,
        )

    stream = output
    try:
        while True:
            live = probe_live_memory(
                vehicle_id=vehicle_id,
                vehicle=vehicle,
                timeout_s=timeout_s,
            )
            if json_output:
                line = json.dumps(live, sort_keys=True)
            else:
                line = _format_live_memory_screen(vehicle_id=vehicle_id, live=live)
            if stream is not None:
                if not no_clear and not json_output:
                    print("\033[2J\033[H", end="", file=stream)
                print(line, file=stream, flush=True)
            if once:
                return CommandResult(
                    *once_stream_outcome(step="memory", live=live, line=line, stream=stream)
                )
            time.sleep(max(0.1, float(refresh_s)))
    except KeyboardInterrupt:
        return CommandResult(130, "")


def _stream_physical_memory_with_inspector(
    *,
    vehicle_id: str,
    vehicle: dict[str, Any],
    refresh_s: float,
    once: bool,
    no_clear: bool,
    timeout_s: float,
    output: TextIO | None,
) -> CommandResult:
    """Poll status, feed the shared loopback publication, and serve the /memory inspector."""

    stream = output
    base_url = picar_base_url(vehicle)
    if not base_url:
        return CommandResult(2, f"Vehicle {vehicle_id!r} has no picar base_url connection.")

    runtime_dir = physical_observation_dir(vehicle_id)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    frame_path = runtime_dir / "latest_frame.jpg"
    view_server: RuntimeViewServer | None = None
    view_error: str | None = None
    try:
        view_server = RuntimeViewServer(
            vehicle_id=vehicle_id,
            automation_dir=runtime_dir,
        ).start()
    except OSError as exc:
        view_error = f"{type(exc).__name__}: {exc}"

    try:
        while True:
            live = probe_live_memory(
                vehicle_id=vehicle_id,
                vehicle=vehicle,
                timeout_s=timeout_s,
            )
            publication: dict[str, Any] | None = None
            fetch_error: str | None = None
            try:
                publication = fetch_observation_publication(base_url, timeout_s=timeout_s)
            except ConnectionError as exc:
                fetch_error = str(exc)

            memory_view_url = None
            if view_server is not None and view_server.url:
                memory_view_url = view_server.url.rstrip("/") + "/memory"
            if publication is not None and view_server is not None:
                try:
                    _publish_physical_view(
                        view_server=view_server,
                        base_url=base_url,
                        publication=publication,
                        frame_path=frame_path,
                        timeout_s=timeout_s,
                    )
                    memory_view_url = view_server.url.rstrip("/") + "/memory"
                    view_error = None
                except (ConnectionError, OSError, TypeError, ValueError) as exc:
                    view_error = f"{type(exc).__name__}: {exc}"

            if stream is not None:
                if not no_clear:
                    print("\033[2J\033[H", end="", file=stream)
                lines = [
                    _format_live_memory_screen(vehicle_id=vehicle_id, live=live),
                    "",
                ]
                if memory_view_url:
                    lines.append(f"memory map: {memory_view_url}")
                    lines.append("perception view: " + memory_view_url.rsplit("/", 1)[0] + "/perception")
                elif view_error:
                    lines.append(f"memory map: unavailable ({view_error})")
                else:
                    lines.append("memory map: unavailable")
                if fetch_error:
                    lines.append(f"publication: {fetch_error}")
                # The published memory report: each plugin's ledger and the publisher.
                elif isinstance(publication, dict) and isinstance(publication.get("memory"), dict):
                    report = publication["memory"]
                    lines.append(
                        "publication memory: evidence publisher "
                        f"{evidence_publisher(report) or 'none'}"
                    )
                    lines.extend(_format_plugin_ledgers(plugin_ledgers(report)))
                print("\n".join(lines), file=stream, flush=True)

            if once:
                return CommandResult(
                    *once_stream_outcome(step="memory", live=live, line="", stream=stream)
                )
            time.sleep(max(0.1, float(refresh_s)))
    except KeyboardInterrupt:
        return CommandResult(130, "")
    finally:
        if view_server is not None:
            view_server.stop()


def probe_live_memory(
    *,
    vehicle_id: str,
    vehicle: dict[str, Any] | None = None,
    timeout_s: float = 3.0,
) -> dict[str, Any]:
    """Return a normalized live-memory probe without requiring stream mode."""

    return probe_live_step("memory", vehicle_id=vehicle_id, vehicle=vehicle, timeout_s=timeout_s)


def probe_live_step(
    step: str,
    *,
    vehicle_id: str,
    vehicle: dict[str, Any] | None = None,
    timeout_s: float = 3.0,
) -> dict[str, Any]:
    """``step`` as the running autonomy engine has it: its runner's status.

    The PiCar reports it under ``/autonomy/status``; a Chase automation
    worker under its step in ``state.json``. That is what the engine runs,
    not what any view last rendered.
    """

    schema = LIVE_STEP_SCHEMAS[step]
    if vehicle is None:
        discovery = discover_active_vehicles(
            timeout_s=timeout_s,
            include_picar=True,
            include_chase_sim=True,
            include_inactive=True,
        )
        vehicle, error = find_vehicle_by_id(discovery, vehicle_id)
        if error or vehicle is None:
            return {
                "schema": schema,
                "vehicle_id": vehicle_id,
                "status": "unavailable",
                "error": error or f"Vehicle {vehicle_id!r} was not found.",
                "probed_at_ms": int(time.time() * 1000),
            }

    provider = vehicle.get("provider")
    if provider == "picar":
        return _probe_physical_step(step, vehicle_id=vehicle_id, vehicle=vehicle, timeout_s=timeout_s)
    if provider == "chase-sim":
        return _probe_chase_step(step, vehicle_id=vehicle_id)
    return {
        "schema": schema,
        "vehicle_id": vehicle_id,
        "status": "unavailable",
        "error": (
            f"Vehicle {vehicle_id!r} is provider {provider!r}; "
            f"live {step} supports picar and chase-sim."
        ),
        "probed_at_ms": int(time.time() * 1000),
    }


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
            last_duration_ms=status.get("last_duration_ms"),
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


def _probe_physical_step(
    step: str,
    *,
    vehicle_id: str,
    vehicle: dict[str, Any],
    timeout_s: float,
) -> dict[str, Any]:
    base_url = picar_base_url(vehicle)
    probed_at_ms = int(time.time() * 1000)
    if not base_url:
        return {
            "schema": LIVE_STEP_SCHEMAS[step],
            "vehicle_id": vehicle_id,
            "provider": "picar",
            "status": "unavailable",
            "error": f"Vehicle {vehicle_id!r} has no picar base_url connection.",
            "probed_at_ms": probed_at_ms,
        }
    try:
        status = fetch_autonomy_status(base_url, timeout_s=timeout_s)
    except ConnectionError as exc:
        return {
            "schema": LIVE_STEP_SCHEMAS[step],
            "vehicle_id": vehicle_id,
            "provider": "picar",
            "status": "error",
            "endpoint": f"{base_url}/autonomy/status",
            "error": str(exc),
            "probed_at_ms": probed_at_ms,
        }

    autonomy = status.get("autonomy") if isinstance(status.get("autonomy"), dict) else {}
    steps = autonomy.get("steps") if isinstance(autonomy.get("steps"), dict) else {}
    runner = steps.get(step) if isinstance(steps.get(step), dict) else None
    last_control = autonomy.get("last_control") if isinstance(autonomy.get("last_control"), dict) else {}
    control_meta = (
        last_control.get("metadata") if isinstance(last_control.get("metadata"), dict) else {}
    )
    # Whether the engine's last cycle saw memory; only memory's probe reports it.
    has_memory = (
        {"has_memory": bool(control_meta.get("has_memory"))} if step == "memory" else {}
    )
    if runner is None:
        return {
            "schema": LIVE_STEP_SCHEMAS[step],
            "vehicle_id": vehicle_id,
            "provider": "picar",
            "status": "absent",
            "endpoint": f"{base_url}/autonomy/status",
            "drive_mode": status.get("drive_mode"),
            **has_memory,
            "error": (
                f"No live {step} step in /autonomy/status. "
                "If activation was deployed, update core then autonomy with --restart."
            ),
            "probed_at_ms": probed_at_ms,
        }

    return {
        "schema": LIVE_STEP_SCHEMAS[step],
        "vehicle_id": vehicle_id,
        "provider": "picar",
        "status": "live",
        "endpoint": f"{base_url}/autonomy/status",
        "drive_mode": status.get("drive_mode"),
        **has_memory,
        **_live_step_fields(step, runner),
        "probed_at_ms": probed_at_ms,
    }


def _probe_chase_memory(*, vehicle_id: str) -> dict[str, Any]:
    return _probe_chase_step("memory", vehicle_id=vehicle_id)


def _probe_chase_step(step: str, *, vehicle_id: str) -> dict[str, Any]:
    probed_at_ms = int(time.time() * 1000)
    automation_dir = _automation_dir(vehicle_id)
    state_path = automation_dir / "state.json"
    if not state_path.exists():
        return {
            "schema": LIVE_STEP_SCHEMAS[step],
            "vehicle_id": vehicle_id,
            "provider": "chase-sim",
            "status": "unavailable",
            "error": (
                f"No automation runtime state for {vehicle_id!r}. "
                f"Run: ./cli/automa vehicles automation run --id {vehicle_id}"
            ),
            "probed_at_ms": probed_at_ms,
        }
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "schema": LIVE_STEP_SCHEMAS[step],
            "vehicle_id": vehicle_id,
            "provider": "chase-sim",
            "status": "error",
            "error": f"Could not read automation state: {exc}",
            "probed_at_ms": probed_at_ms,
        }
    if not isinstance(state, dict):
        return {
            "schema": LIVE_STEP_SCHEMAS[step],
            "vehicle_id": vehicle_id,
            "provider": "chase-sim",
            "status": "error",
            "error": "Automation state is not a JSON object.",
            "probed_at_ms": probed_at_ms,
        }

    liveness = assess_chase_worker_liveness(
        state=state,
        probed_at_ms=probed_at_ms,
        step=step,
        vehicle_id=vehicle_id,
    )
    if not liveness["live"]:
        return {
            "schema": LIVE_STEP_SCHEMAS[step],
            "vehicle_id": vehicle_id,
            "provider": "chase-sim",
            "status": liveness["status"],
            "error": liveness["error"],
            "probed_at_ms": probed_at_ms,
            "worker_status": state.get("status"),
            "worker_pid": liveness.get("pid"),
            "worker_updated_at_ms": liveness.get("updated_at_ms"),
            "max_age_ms": CHASE_WORKER_PROBE_MAX_AGE_MS,
        }

    entry = state.get(step) if isinstance(state.get(step), dict) else None
    if entry is None or entry.get("status") == "absent":
        return {
            "schema": LIVE_STEP_SCHEMAS[step],
            "vehicle_id": vehicle_id,
            "provider": "chase-sim",
            "status": "absent",
            "error": (
                f"Automation worker has no live {step} step. "
                f"Stage {step} then restart automation: "
                f"./cli/automa vehicles update {step} --id {vehicle_id}"
            ),
            "probed_at_ms": probed_at_ms,
            f"worker_{step}": entry,
            "worker_status": state.get("status"),
            "worker_pid": liveness.get("pid"),
        }

    status_block = entry.get("status") if isinstance(entry.get("status"), dict) else entry
    if not isinstance(status_block, dict):
        status_block = {}
    return {
        "schema": LIVE_STEP_SCHEMAS[step],
        "vehicle_id": vehicle_id,
        "provider": "chase-sim",
        "status": "live",
        **_live_step_fields(step, status_block),
        "activation": entry.get("activation") or status_block.get("activation"),
        "probed_at_ms": probed_at_ms,
        "worker_status": state.get("status"),
        "worker_pid": liveness.get("pid"),
        "run_id": state.get("run_id"),
        "worker_updated_at_ms": liveness.get("updated_at_ms"),
    }


def _format_live_memory_screen(*, vehicle_id: str, live: dict[str, Any]) -> str:
    return format_live_step_screen("memory", vehicle_id=vehicle_id, live=live)


def format_live_step_screen(step: str, *, vehicle_id: str, live: dict[str, Any]) -> str:
    status = str(live.get("status") or "unknown")
    lines = [
        f"Live {step}: {vehicle_id} [{status}]",
    ]
    if live.get("provider"):
        lines.append(f"Provider: {live.get('provider')}")
    if live.get("endpoint"):
        lines.append(f"Endpoint: {live.get('endpoint')}")
    if live.get("drive_mode") is not None:
        lines.append(f"Drive mode: {live.get('drive_mode')}")
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
            lines.append(f"Last update duration: {live.get('last_duration_ms')} ms")
        if live.get("last_error"):
            lines.append(f"Last error: {live.get('last_error')}")
        if live.get("has_memory") is not None:
            lines.append(f"Engine saw memory: {live.get('has_memory')}")
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

