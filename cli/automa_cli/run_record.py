"""The run record and terminal lines every vehicle's automation run shares.

Every vehicle's runtime host executes the decision cycle while the CLI
monitors it (``runtime_monitor``), writing this ``state.json`` record and
printing these lines. A recording also appends the same ``manifest.json``,
which replay loads.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from autonomy.runtime.session import RunConfiguration
from .paths import display_path
from .runtime_view import RuntimeViewServer

RUN_STATE_SCHEMA = "automa_automation_run_state_v0"
READINESS_SCHEMA = "automa_cli_readiness_v1"
# Every vehicle's runtime host owns its wheel commands during a run.
CONTROL_SOURCE_LABELS = {"runtime_host": "vehicle runtime host"}


def timestamp_ms() -> int:
    return int(time.time() * 1000)


def control_source(configuration: RunConfiguration) -> str:
    return "runtime_host"


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
        "recorded_count": 0,
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
    """Record the steps, each staged step's report, and the decision the host applied."""

    steps = host_status.get("steps") if isinstance(host_status.get("steps"), dict) else {}
    state["steps"] = step_status(host_status)
    applied = host_status.get("applied_decision")
    if isinstance(applied, dict) and applied.get("generation_id") and isinstance(applied.get("steps"), dict):
        state["decision"].update(
            generation_id=applied["generation_id"], steps=applied["steps"],
            published=applied["steps"].get("proposal") is not None,
        )
    if "arming" in host_status:
        state["arming"] = host_status["arming"]
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

    if status == "completed" and state["recording"] and state["recorded_count"] != state["processed_count"]:
        status, error = "error", "Recording count does not match completed decisions"
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
                *([f"Decisions recorded: {state['recorded_count']}"] if state["recording"] else []),
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
