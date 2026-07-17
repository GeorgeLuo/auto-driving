from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from .automation import _automation_dir, _pid_alive
from .paths import display_path
from .perception_view import PerceptionViewServer
from .physical_observation import (
    LATEST_FRAME_PATH,
    LATEST_JSON_PATH,
    fetch_observation_frame,
    fetch_observation_publication,
    perception_text_from_publication,
    physical_observation_dir,
    picar_base_url,
    publication_to_frame_record,
)
from .vehicles import discover_active_vehicles, find_vehicle_by_id, format_active_vehicles_snapshot


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
    output: TextIO | None = None,
) -> CommandResult:
    payload = discover_active_vehicles(
        timeout_s=timeout_s,
        include_picar=True,
        include_chase_sim=True,
        include_inactive=True,
    )
    vehicle, error = find_vehicle_by_id(payload, vehicle_id)
    if error:
        return CommandResult(
            2,
            "\n\n".join(
                [
                    error,
                    "Discovery snapshot:",
                    format_active_vehicles_snapshot(payload, include_inactive=True),
                ]
            ),
        )
    if vehicle is None:
        return CommandResult(2, f"Vehicle {vehicle_id!r} was not found.")

    provider = vehicle.get("provider")
    if provider == "chase-sim":
        return _stream_chase_perception(
            vehicle_id=vehicle_id,
            refresh_s=refresh_s,
            once=once,
            no_clear=no_clear,
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
            output=output,
        )
    return CommandResult(
        2,
        f"Vehicle {vehicle_id!r} is provider {provider!r}; perception stream supports chase-sim and picar.",
    )


def _stream_chase_perception(
    *,
    vehicle_id: str,
    refresh_s: float,
    once: bool,
    no_clear: bool,
    output: TextIO | None,
) -> CommandResult:
    stream = output
    automation_dir = _automation_dir(vehicle_id)
    state_path = automation_dir / "state.json"
    process_path = automation_dir / "process.json"
    latest_text_path = automation_dir / "latest_perception.txt"
    latest_json_path = automation_dir / "latest_perception.json"
    default_log_path = automation_dir / "automation.log"

    if not automation_dir.exists():
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
            state = _read_json(state_path)
            process = _read_json(process_path)
            latest = _read_json(latest_json_path)
            latest_text = _read_text(latest_text_path)

            if stream is not None:
                if not no_clear:
                    print("\033[2J\033[H", end="", file=stream)
                print(
                    _render_chase_perception_screen(
                        vehicle_id=vehicle_id,
                        state=state,
                        process=process,
                        latest=latest,
                        latest_text=latest_text,
                        state_path=state_path,
                        default_log_path=default_log_path,
                    ),
                    file=stream,
                    flush=True,
                )

            if once:
                return CommandResult(0, "")
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
    output: TextIO | None,
) -> CommandResult:
    stream = output
    base_url = picar_base_url(vehicle)
    if not base_url:
        return CommandResult(2, f"Vehicle {vehicle_id!r} has no picar base_url connection.")

    runtime_dir = physical_observation_dir(vehicle_id)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    frame_path = runtime_dir / "latest_frame.jpg"
    view_server: PerceptionViewServer | None = None
    view_error: str | None = None
    try:
        view_server = PerceptionViewServer(
            vehicle_id=vehicle_id,
            automation_dir=runtime_dir,
        ).start()
    except OSError as exc:
        view_error = f"{type(exc).__name__}: {exc}"

    try:
        while True:
            publication: dict[str, Any] | None = None
            fetch_error: str | None = None
            try:
                publication = fetch_observation_publication(base_url, timeout_s=timeout_s)
            except ConnectionError as exc:
                fetch_error = str(exc)

            view_url = view_server.url if view_server is not None else None
            if publication is not None and view_server is not None:
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

            if stream is not None:
                if not no_clear:
                    print("\033[2J\033[H", end="", file=stream)
                print(
                    _render_physical_perception_screen(
                        vehicle_id=vehicle_id,
                        base_url=base_url,
                        publication=publication,
                        fetch_error=fetch_error,
                        view_url=view_url,
                        view_error=view_error,
                    ),
                    file=stream,
                    flush=True,
                )

            if once:
                if fetch_error is not None:
                    return CommandResult(2, fetch_error)
                return CommandResult(0, "")
            time.sleep(max(0.1, float(refresh_s)))
    except KeyboardInterrupt:
        return CommandResult(130, "")
    finally:
        if view_server is not None:
            view_server.stop()


def _publish_physical_view(
    *,
    view_server: PerceptionViewServer,
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
    view_server.publish_frame(frame_path=frame_path, frame_record=frame_record)
    view_server.publish_perception(frame_record=frame_record)


def _render_chase_perception_screen(
    *,
    vehicle_id: str,
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
    pid = process.get("pid") if isinstance(process.get("pid"), int) else state.get("pid")
    pid_state = "unknown"
    if isinstance(pid, int):
        pid_state = "alive" if _pid_alive(pid) else "not running"

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
        f"status: {state.get('status', 'unknown')}  pid: {pid or 'unknown'} ({pid_state})",
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
    body = latest_text.strip() if latest_text.strip() else "(no latest perception yet)"
    return "\n".join([*header, body])


def _render_physical_perception_screen(
    *,
    vehicle_id: str,
    base_url: str,
    publication: dict[str, Any] | None,
    fetch_error: str | None,
    view_url: str | None,
    view_error: str | None,
) -> str:
    if fetch_error is not None:
        body = fetch_error
        health = "unavailable"
        frame_id = "none"
        age_ms: Any = "unknown"
        thing_count: Any = "unknown"
        signal_count: Any = "unknown"
        algorithm = "unknown"
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
        algorithm = publication.get("algorithm") or "unknown"
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
        f"status: {health}  drive_mode: {mode}  algorithm: {algorithm}",
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



