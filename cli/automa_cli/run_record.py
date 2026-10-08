"""The run record and terminal lines every vehicle's automation run shares.

A Chase run hosts the decision cycle in the CLI worker; a PiCar run hosts it
onboard while the CLI monitors it. Both write this ``state.json`` record and
print these lines, so a run reads the same on either vehicle. A recording also
appends the same ``manifest.json``, which replay loads.
"""
from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from typing import Any

from autonomy.runtime.session import RunConfiguration

from .paths import display_path
from .runtime_view import RuntimeViewServer
from .staged_bundle import write_json_atomically

RUN_STATE_SCHEMA = "automa_automation_run_state_v0"
RECORDING_MANIFEST_SCHEMA = "automa_recording_manifest_v0"
RECORDING_MANIFEST_NAME = "manifest.json"
READINESS_SCHEMA = "automa_cli_readiness_v1"
# Where wheel commands come from during a run: Chase's WebSocket input, the
# simulator's own source while observing, or the PiCar's onboard host.
CONTROL_SOURCE_LABELS = {
    "external_ws": "external WS",
    "preserved_current": "preserved current simulator source",
    "onboard": "onboard Donkey host",
}


def timestamp_ms() -> int:
    return int(time.time() * 1000)


def _recording_text(value: Any, label: str) -> str:
    if type(value) is not str or not value or any(char.isspace() for char in value):
        raise ValueError(f"{label} must be a nonempty string")
    return value


def append_recording_frame(
    run_dir: Path,
    *,
    vehicle_id: str,
    run_id: str,
    generation_id: str,
    frame_id: str,
    timestamp_ms: int,
    image_path: Path,
    steps: dict[str, Any],
    frame_index: int | None = None,
) -> None:
    """Append one recorded frame to the run directory's manifest.

    Chase and PiCar both call this. The image path is stored relative to the
    run directory, which is the directory replay loads. The frame keeps the
    report identity, the original capture time, and the staged step selections.
    """

    vehicle_id = _recording_text(vehicle_id, "vehicle_id")
    run_id = _recording_text(run_id, "run_id")
    generation_id = _recording_text(generation_id, "generation_id")
    frame_id = _recording_text(frame_id, "frame_id")
    if type(timestamp_ms) is not int or timestamp_ms < 0:
        raise ValueError("timestamp_ms must be a nonnegative int")
    if type(steps) is not dict:
        raise ValueError("steps must be the staged step selections")
    if frame_index is not None and (type(frame_index) is not int or frame_index < 0):
        raise ValueError("frame_index must be a nonnegative int")
    root = Path(run_dir)
    root.mkdir(parents=True, exist_ok=True)
    candidate = Path(image_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        relative_image = candidate.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError("recorded image must stay inside the run directory") from exc
    entry: dict[str, Any] = {
        "vehicle_id": vehicle_id,
        "run_id": run_id,
        "generation_id": generation_id,
        "frame_id": frame_id,
        "timestamp_ms": timestamp_ms,
        "image_path": relative_image,
        "steps": copy.deepcopy(steps),
    }
    if frame_index is not None:
        entry["frame_index"] = frame_index
    manifest_path = root / RECORDING_MANIFEST_NAME
    payload: dict[str, Any] = {
        "schema": RECORDING_MANIFEST_SCHEMA,
        "vehicle_id": vehicle_id,
        "run_id": run_id,
        "frames": [],
    }
    if manifest_path.is_file():
        loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("recording manifest must be a JSON object")
        payload = loaded
    frames = payload.get("frames")
    if not isinstance(frames, list):
        frames = []
    for index, item in enumerate(frames):
        if isinstance(item, dict) and item.get("frame_id") == frame_id:
            frames[index] = entry
            break
    else:
        frames.append(entry)
    payload["schema"] = RECORDING_MANIFEST_SCHEMA
    payload["vehicle_id"] = vehicle_id
    payload["run_id"] = run_id
    payload["frames"] = frames
    write_json_atomically(manifest_path, payload)


def control_source(configuration: RunConfiguration, *, onboard: bool) -> str:
    if onboard:
        return "onboard"
    return "external_ws" if configuration.mode == "autonomy" else "preserved_current"


def control_application(configuration: RunConfiguration) -> str:
    return "shared_execution" if configuration.mode == "autonomy" else "not_applied"


def new_run_state(
    *,
    vehicle_id: str,
    run_id: str | None,
    status: str,
    pid: int | None,
    configuration: RunConfiguration,
    record: bool,
    control_source: str,
    automation_dir: Path,
    front_camera_path: Path,
    run_dir: Path | None,
    published_view: dict[str, Any],
    started_at_ms: int | None = None,
) -> dict[str, Any]:
    started_at_ms = timestamp_ms() if started_at_ms is None else started_at_ms
    return {
        "schema": RUN_STATE_SCHEMA,
        "vehicle_id": vehicle_id,
        "run_id": run_id,
        "status": status,
        "pid": pid,
        "started_at_ms": started_at_ms,
        "updated_at_ms": started_at_ms,
        "frames_captured": 0,
        "processed_count": 0,
        "skipped_count": 0,
        "num_decisions": configuration.num_decisions,
        "interval_s": configuration.interval_s,
        "pipeline": "latest_frame_async_decision",
        "control_source": control_source,
        "action_policy": configuration.mode,
        "control_application": control_application(configuration),
        "recording": record,
        "run_dir": display_path(run_dir) if run_dir is not None else None,
        "latest": {
            "front_camera": None if record else display_path(front_camera_path),
            "perception_json": display_path(automation_dir / "latest_perception.json"),
            "perception_text": display_path(automation_dir / "latest_perception.txt"),
        },
        "published_view": published_view,
        "readiness": starting_readiness(),
    }


def _readiness(
    status: str, ready_for: str, gates: dict[str, Any], blocking_layer: str | None
) -> dict[str, Any]:
    return {
        "schema": READINESS_SCHEMA,
        "status": status,
        "ready_for": ready_for,
        "checked_at_ms": timestamp_ms(),
        "gates": gates,
        "blocking_layer": blocking_layer,
    }


def starting_readiness() -> dict[str, Any]:
    return _readiness(
        "blocked",
        "inspect perception",
        {
            "sensor_capture": {"status": "incomplete"},
            "perception": {"status": "incomplete"},
            "perception_view": {"status": "incomplete"},
        },
        "sensor_capture",
    )


def frame_readiness(view_ready: bool) -> dict[str, Any]:
    return _readiness(
        "ready" if view_ready else "blocked",
        "inspect perception",
        {
            "sensor_capture": {"status": "ready"},
            "perception": {"status": "ready"},
            "perception_view": {"status": "ready" if view_ready else "blocked"},
        },
        None if view_ready else "perception_view",
    )


def stopped_readiness() -> dict[str, Any]:
    return _readiness(
        "ready",
        "inspect stopped deployment",
        {
            "automation_worker": {"status": "stopped"},
            "perception_view": {"status": "not_current"},
        },
        None,
    )


def failed_readiness(blocking_layer: str) -> dict[str, Any]:
    return _readiness("blocked", "inspect perception", {}, blocking_layer)


def perception_record(activation_path: Path, activation: Any) -> dict[str, Any]:
    return {
        "activation": display_path(activation_path),
        "preset": activation.metadata.get("preset"),
        "plugins": list(activation.plugins),
    }


def decision_record(identity: dict[str, Any]) -> dict[str, Any]:
    return {
        "generation_id": identity["generation_id"],
        "steps": identity["steps"],
        # A decision frame is published only when proposals are staged.
        "published": identity["steps"]["proposal"] is not None,
        "latest_frame_publish_skips": 0,
        "latest_frame_publish_skip_reason": None,
    }


def step_status(host_status: dict[str, Any]) -> dict[str, Any]:
    """Each step's plugin IDs and last error, without the per-cycle records."""

    steps = host_status.get("steps") if isinstance(host_status.get("steps"), dict) else {}
    return {
        step: (
            None
            if not isinstance(item, dict)
            else {
                "plugin_ids": item.get("plugin_ids"),
                "last_error": item.get("last_error"),
            }
        )
        for step, item in steps.items()
    } | {
        "cycle_count": host_status.get("cycle_count"),
        "error_count": host_status.get("error_count"),
    }


def record_host_status(
    state: dict[str, Any], host_status: dict[str, Any], *, activations: dict[str, Path]
) -> None:
    """Record the steps, and each staged step's report, from one host status."""

    steps = host_status.get("steps") if isinstance(host_status.get("steps"), dict) else {}
    state["steps"] = step_status(host_status)
    for step, path in activations.items():
        state[step] = {"activation": display_path(path), "status": steps.get(step) or "absent"}


def startup_lines(state: dict[str, Any]) -> list[str]:
    perception = state["perception"]
    view = state["published_view"]
    source = state["control_source"]
    return [
        f"Automation running: {state['vehicle_id']}",
        f"Perception: {perception['preset'] or ', '.join(perception['plugins']) or '(none)'}",
        (
            f"Recording: {state['run_dir']}"
            if state["run_dir"] is not None
            else "Recording: off; latest frame and perception are overwritten each iteration"
        ),
        f"Control source: {CONTROL_SOURCE_LABELS.get(source, source)}",
        f"Action policy: {state['action_policy']}",
        f"Decision generation: {state['decision']['generation_id']}",
        (
            f"Runtime view: {view.get('url')}"
            if view.get("available")
            else f"Runtime view: unavailable ({view.get('reason', 'startup failed')})"
        ),
        f"Decisions: {state['num_decisions']}" if state["num_decisions"] else "Decisions: unbounded",
    ]


def reports_frame(count: int, *, verbose: bool) -> bool:
    return verbose or count == 1 or count % 10 == 0


def frame_line(
    frame_id: str, *, signals: int, things: int, action: Any,
    skipped_since_previous: int | None = None,
) -> str:
    line = f"{frame_id}: signals={signals} things={things} action={action}"
    if skipped_since_previous:
        line += f" skipped_since_previous={skipped_since_previous}"
    return line


def stop_view(view_server: RuntimeViewServer | None) -> dict[str, Any]:
    """Stop the run's view and return its last published record."""

    if view_server is None:
        return {
            "status": "unavailable",
            "available": False,
            "url": None,
            "reason": "perception view did not start",
        }
    try:
        view_server.stop()
    except OSError as exc:
        return {
            **view_server.describe(status="error"),
            "reason": f"{type(exc).__name__}: {exc}",
        }
    try:
        record = json.loads(view_server.record_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        record = None
    return record if isinstance(record, dict) else view_server.describe(status="stopped")


def finish_run(
    state: dict[str, Any],
    view_server: RuntimeViewServer | None,
    *,
    status: str,
    stop_reason: str | None = None,
    error: str | None = None,
    error_code: str | None = None,
    error_details: dict[str, Any] | None = None,
    blocking_layer: str = "automation_worker",
) -> None:
    """Close the run's view and record how the run ended."""

    state["status"] = status
    if stop_reason is not None:
        state["stop_reason"] = stop_reason
    if status == "error":
        state["error"] = error
        if error_code is not None:
            state["error_code"] = error_code
            state["error_details"] = error_details
        state["readiness"] = failed_readiness(blocking_layer)
    else:
        state["readiness"] = stopped_readiness()
    state["completed_at_ms"] = state["updated_at_ms"] = timestamp_ms()
    state["published_view"] = stop_view(view_server)


def run_result(state: dict[str, Any], *, state_path: Path) -> tuple[int, str]:
    """The exit code and closing lines for a finished run."""

    vehicle_id = state["vehicle_id"]
    status = state["status"]
    if status == "error":
        return 2, "\n".join(
            [
                f"Automation failed for {vehicle_id}.",
                f"Reason: {state['error']}",
                f"State: {display_path(state_path)}",
                "Not ready for: inspect perception",
            ]
        )
    if status == "completed":
        return 0, "\n".join(
            [
                f"Automation completed: {vehicle_id}",
                f"Frames captured: {state['frames_captured']}",
                f"Decisions completed: {state['processed_count']}",
                f"Frames superseded before decision: {state['skipped_count']}",
                f"Control source: {state['control_source']}",
                f"Action policy: {state['action_policy']}",
                f"Recording: {'on' if state['recording'] else 'off'}",
                f"State: {display_path(state_path)}",
                f"Latest perception: {state['latest']['perception_text']}",
                "Ready for: inspect stopped deployment",
            ]
        )
    code = 130 if state.get("stop_reason") == "keyboard_interrupt" else 0
    return code, "\n".join(
        [
            f"Automation stopped: {vehicle_id}",
            f"State: {display_path(state_path)}",
            "Ready for: inspect stopped deployment",
        ]
    )
