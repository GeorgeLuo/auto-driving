"""The run record every host's run shares.

Every runtime host executes the decision cycle while the CLI monitors it
(``monitor``), writing this ``state.json`` record. A recording also appends
the same ``manifest.json``, which replay loads. The lines a terminal prints
come from ``render``.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from autonomy.runtime.session import RunConfiguration

from ...shared.io.json_files import read_json, write_json_atomically
from ...shared.paths import display_path
from ...shared.timestamps import timestamp_ms
from ..host_formatting import perception_stream_command
from ..publications.host_publications import observation_component
from ..staging import automation_dir as host_automation_dir
from .models import RunResult

RUN_STATE_SCHEMA = "automa_automation_run_state"


# Every run's host applies its own wheel commands.
CONTROL_SOURCE = "runtime_host"


def control_application(configuration: RunConfiguration) -> str:
    return "shared_execution" if configuration.mode == "autonomy" else "not_applied"


def new_run_state(
    *,
    host_name: str,
    run_id: str | None,
    status: str,
    pid: int | None,
    configuration: RunConfiguration,
    record: bool,
    control_source: str,
    run_dir: Path | None,
    started_at_ms: int | None = None,
) -> dict[str, Any]:
    started_at_ms = timestamp_ms() if started_at_ms is None else started_at_ms
    return {
        "schema": RUN_STATE_SCHEMA,
        "host_name": host_name,
        "run_id": run_id,
        "status": status,
        "pid": pid,
        "started_at_ms": started_at_ms,
        "updated_at_ms": started_at_ms,
        "frames_received": 0,
        "frames_dropped": None,
        "processed_count": 0,
        "num_decisions": configuration.num_decisions,
        "pipeline": "latest_frame_async_decision",
        "control_source": control_source,
        "action_policy": configuration.mode,
        "control_application": control_application(configuration),
        "recording": record,
        "recorded_count": 0,
        "run_dir": display_path(run_dir) if run_dir is not None else None,
    }


def decision_record(identity: dict[str, Any]) -> dict[str, Any]:
    return {
        "generation_id": identity["generation_id"],
        "steps": identity["steps"],
        # A decision frame is published only when proposals are staged.
        "published": identity["steps"]["proposal"] is not None,
    }


def step_status(host_status: dict[str, Any]) -> dict[str, Any]:
    """Each step's plugin IDs and configs, preset, run and failure counts and last error, without the per-cycle records.

    A step whose report the host could not read keeps that error.
    """

    steps = host_status.get("steps") if isinstance(host_status.get("steps"), dict) else {}
    return {step: _step_report(item) for step, item in steps.items()} | {
        "cycle_count": host_status.get("cycle_count"),
        "error_count": host_status.get("error_count"),
    }


def _step_report(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    if item.get("status") == "error":
        return {"status": "error", "error": item.get("error")}
    return {
        "plugin_ids": item.get("plugin_ids"),
        "plugin_configs": item.get("plugin_configs"),
        "preset": item.get("preset"),
        "run_count": item.get("run_count"),
        "failure_count": item.get("failure_count"),
        "last_error": item.get("last_error"),
    }


def applied_selections(
    selections: dict[str, dict[str, Any]], steps: Any,
) -> dict[str, dict[str, Any]] | None:
    """``selections`` as a running host reports applying them, when it reports its steps."""

    if not isinstance(steps, dict) or not any(step in steps for step in selections):
        return None
    applied = {}
    for step, selection in selections.items():
        report = steps.get(step) if isinstance(steps.get(step), dict) else {}
        applied[step] = {
            **selection,
            "source": "applied",
            "preset": report.get("preset"),
            "plugins": list(report.get("plugin_ids") or []),
            "plugin_configs": report.get("plugin_configs"),
        }
    return applied


def latest_frame(host_status: dict[str, Any]) -> dict[str, Any] | None:
    """The host's latest observed frame from its ``/host/status`` metadata, or ``None`` before one."""

    latest = observation_component(host_status).get("latest")
    if not isinstance(latest, dict):
        return None
    captured_at_ms, completed_at_ms = latest.get("captured_at_ms"), latest.get("completed_at_ms")
    return {
        "frame_id": latest.get("frame_id"),
        "frame_index": latest.get("frame_index"),
        "captured_at_ms": captured_at_ms,
        "perception_completed_at_ms": completed_at_ms,
        "capture_to_perception_ms": (
            completed_at_ms - captured_at_ms
            if isinstance(completed_at_ms, int) and isinstance(captured_at_ms, int)
            else None
        ),
        "cycle_duration_ms": latest.get("duration_ms"),
    }


def record_host_status(state: dict[str, Any], host_status: dict[str, Any]) -> None:
    """Record the steps and selections the host applied, each selection's report, its decision, and latest frame.

    A run starts with the staged selections; once the host reports its
    steps, the selections are what it runs, plugins armed since it started
    included.
    """

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
    for step, selection in state["selections"].items():
        selection["status"] = steps.get(step) or "absent"
    state["selections"] = applied_selections(state["selections"], steps) or state["selections"]
    frame = latest_frame(host_status)
    if frame is not None:
        state["last_frame"] = frame


def finish_run(
    state: dict[str, Any],
    *,
    status: str,
    stop_reason: str | None = None,
    error: str | None = None,
) -> None:
    """Record how the run ended; ``hosts run`` reports this status once it is set.

    An incomplete recording export fails the run even when its session completed.
    """

    if status == "completed" and state["recording"] and state["recorded_count"] != state["processed_count"]:
        status, error = "error", "Recording count does not match completed decisions"
    state["status"] = status
    if stop_reason is not None:
        state["stop_reason"] = stop_reason
    if status == "error":
        state["error"] = error
    state["completed_at_ms"] = state["updated_at_ms"] = timestamp_ms()


def mark_process_stopped(path: Path, process: Any, *, stopped_by: str) -> None:
    record = process if isinstance(process, dict) else {}
    updated = {
        **record,
        "status": "stopped",
        "stopped_by": stopped_by,
        "stopped_at_ms": timestamp_ms(),
    }
    write_json_atomically(path, updated)


def mark_state_stopped(path: Path, *, stopped_by: str) -> None:
    state = read_json(path)
    if not isinstance(state, dict):
        return
    if state.get("status") not in ("starting", "running", "error"):
        return
    updated = {
        **state,
        "status": "stopped",
        "stop_reason": stopped_by,
        "completed_at_ms": timestamp_ms(),
        "updated_at_ms": timestamp_ms(),
    }
    write_json_atomically(path, updated)


def record_host_automation_terminal_result(
    *,
    host_name: str,
    result: RunResult,
) -> None:
    """Persist failures that occur before the worker creates its normal run state."""

    if result.exit_code == 0:
        return
    automation_dir = host_automation_dir(host_name)
    state_path = automation_dir / "state.json"
    state = read_json(state_path)
    if not isinstance(state, dict) or state.get("status") != "starting":
        return
    completed_at_ms = timestamp_ms()
    write_json_atomically(
        state_path,
        {
            **state,
            "status": "error",
            "pid": os.getpid(),
            "error": result.message or f"automation worker exited with code {result.exit_code}",
            **({"activation_problems": result.problems} if result.problems else {}),
            "exit_code": result.exit_code,
            "completed_at_ms": completed_at_ms,
            "updated_at_ms": completed_at_ms,
        },
    )


def initialize_automation_startup(
    *,
    automation_dir: Path,
    host_name: str,
    started_at_ms: int,
    configuration: RunConfiguration,
    record: bool,
) -> None:
    write_json_atomically(
        automation_dir / "state.json",
        new_run_state(
            host_name=host_name,
            run_id="starting",
            status="starting",
            pid=None,
            configuration=configuration,
            record=record,
            control_source=CONTROL_SOURCE,
            run_dir=None,
            started_at_ms=started_at_ms,
        ),
    )


def mark_automation_startup_error(
    *,
    automation_dir: Path,
    error: str,
    exit_code: int | None = None,
) -> None:
    state_path = automation_dir / "state.json"
    state = read_json(state_path)
    state = state if isinstance(state, dict) else {}
    completed_at_ms = timestamp_ms()
    write_json_atomically(
        state_path,
        {
            **state,
            "status": "error",
            "error": error,
            "exit_code": exit_code,
            "completed_at_ms": completed_at_ms,
            "updated_at_ms": completed_at_ms,
        },
    )


def run_directory(host_name: str, *, create: bool = False) -> Path:
    directory = host_automation_dir(host_name)
    if create:
        directory.mkdir(parents=True, exist_ok=True)
    return directory


def read_process_record(automation_dir: Path) -> Any:
    return read_json(automation_dir / "process.json")


def read_run_state(automation_dir: Path) -> Any:
    return read_json(automation_dir / "state.json")


def record_background_worker(
    automation_dir: Path, *, host_name: str, pid: int, started_at_ms: int,
    command: list[str], log_to_disk: bool,
) -> dict[str, Any]:
    """Record the launched worker before ``hosts run`` waits for its session."""

    log_path = automation_dir / "automation.log"
    record = {
        "schema": "automa_automation_process",
        "host_name": host_name,
        "pid": pid,
        "started_at_ms": started_at_ms,
        "command": command,
        "log_to_disk": bool(log_to_disk),
        "log_path": display_path(log_path) if log_to_disk else None,
        "state_path": display_path(automation_dir / "state.json"),
        "stream_command": perception_stream_command(host_name),
    }
    write_json_atomically(automation_dir / "process.json", record)
    return record


def record_worker_startup(
    automation_dir: Path, process_record: dict[str, Any], startup: dict[str, Any],
) -> dict[str, Any]:
    """Persist the startup verdict without duplicating the full run state."""

    record = {**process_record, "startup": {
        key: value for key, value in startup.items() if key != "state"
    }}
    write_json_atomically(automation_dir / "process.json", record)
    return record
