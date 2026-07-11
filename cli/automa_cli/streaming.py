from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from .automation import _automation_dir, _pid_alive
from .paths import display_path


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
    output: TextIO | None = None,
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
                    _render_perception_screen(
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


def _render_perception_screen(
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
    completed_at = _int_or_none(last_frame.get("perception_completed_at_ms"))
    age_ms = None if completed_at is None else max(0, now_ms - completed_at)

    header = [
        "automa perception stream",
        "",
        f"vehicle: {vehicle_id}",
        f"status: {state.get('status', 'unknown')}  pid: {pid or 'unknown'} ({pid_state})",
        f"control: {state.get('control_source', 'unknown')}  action: {state.get('action_policy', 'unknown')}",
        f"recording: {state.get('recording', 'unknown')}  frames_processed: {state.get('frames_processed', 0)}",
        _cadence_line(state, last_frame, age_ms),
        _latest_line(last_frame, thing_count, perception.get("confidence")),
        f"state: {display_path(state_path)}",
        _log_line(process, default_log_path),
        "",
        "latest perception",
        "-----------------",
    ]
    body = latest_text.strip() if latest_text.strip() else "(no latest perception yet)"
    return "\n".join([*header, body])


def _cadence_line(
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
    thing_count: Any,
    confidence: Any,
) -> str:
    return (
        f"latest: frame={last_frame.get('frame_id', 'none')}  "
        f"captured_at_ms={last_frame.get('captured_at_ms', 'unknown')}  "
        f"things={thing_count if thing_count is not None else 'unknown'}  "
        f"confidence={confidence if confidence is not None else last_frame.get('confidence', 'unknown')}"
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
