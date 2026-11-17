"""The run record every host's run shares.

Every runtime host executes the decision cycle while the CLI monitors it
(``monitor``), writing this ``state.json`` record. Recording appends each
committed frame beside that run's ``manifest.json``, which replay loads. The
host and the CLI export keep separate copies. The lines a terminal prints
come from ``render``.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from autonomy.runtime.session import RunConfiguration

from ...shared.io.json_files import read_json, write_json_atomically
from ...shared.paths import display_path
from ...shared.timestamps import timestamp_ms
from ..host_formatting import perception_stream_command
from ..publications.host_publications import observation_component
from ..staging import automation_dir as host_automation_dir
from .models import RunResult, RunState, WorkerProcess

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
) -> RunState:
    started_at_ms = timestamp_ms() if started_at_ms is None else started_at_ms
    return RunState(
        schema=RUN_STATE_SCHEMA, host_name=host_name, run_id=run_id, status=status, pid=pid,
        started_at_ms=started_at_ms, updated_at_ms=started_at_ms,
        num_decisions=configuration.num_decisions, pipeline="latest_frame_async_decision",
        control_source=control_source, action_policy=configuration.mode,
        control_application=control_application(configuration), recording=record,
        run_dir=display_path(run_dir) if run_dir is not None else None,
    )


def decision_record(identity: dict[str, object]) -> dict[str, object]:
    return {
        "generation_id": identity["generation_id"],
        "steps": identity["steps"],
        # A decision frame is published only when proposals are staged.
        "published": identity["steps"]["proposal"] is not None,
    }


def step_status(host_status: Mapping[str, object]) -> dict[str, object]:
    """Each step's plugin IDs and configs, preset, run and failure counts and last error, without the per-cycle records.

    A step whose report the host could not read keeps that error.
    """

    steps = host_status.get("steps") if isinstance(host_status.get("steps"), dict) else {}
    return {step: _step_report(item) for step, item in steps.items()} | {
        "cycle_count": host_status.get("cycle_count"),
        "error_count": host_status.get("error_count"),
    }


def _step_report(item: object) -> dict[str, object] | None:
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
    selections: dict[str, dict[str, object]], steps: object,
) -> dict[str, dict[str, object]] | None:
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


def latest_frame(host_status: Mapping[str, object]) -> dict[str, object] | None:
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


def record_host_status(state: RunState, host_status: Mapping[str, object]) -> None:
    """Record the steps and selections the host applied, each selection's report, its decision, and latest frame.

    A run starts with the staged selections; once the host reports its
    steps, the selections are what it runs, plugins armed since it started
    included.
    """

    steps = host_status.get("steps") if isinstance(host_status.get("steps"), dict) else {}
    state.steps = step_status(host_status)
    applied = host_status.get("applied_decision")
    if isinstance(applied, dict) and applied.get("generation_id") and isinstance(applied.get("steps"), dict):
        state.decision.update(
            generation_id=applied["generation_id"], steps=applied["steps"],
            published=applied["steps"].get("proposal") is not None,
        )
    if "arming" in host_status:
        state.arming = host_status["arming"]
    for step, selection in state.selections.items():
        selection["status"] = steps.get(step) or "absent"
    state.selections = applied_selections(state.selections, steps) or state.selections
    frame = latest_frame(host_status)
    if frame is not None:
        state.last_frame = frame


def finish_run(
    state: RunState,
    *,
    status: str,
    stop_reason: str | None = None,
    error: str | None = None,
) -> None:
    """Record how the run ended; ``hosts run`` reports this status once it is set.

    An incomplete recording export fails the run even when its session completed.
    """

    if status == "completed" and state.recording and state.recorded_count != state.processed_count:
        status, error = "error", "Recording count does not match completed decisions"
    state.complete(status)
    if stop_reason is not None:
        state.stop_reason = stop_reason
    if status == "error":
        state.error = error


def mark_process_stopped(path: Path, process: WorkerProcess | None, *, stopped_by: str) -> None:
    record = process or WorkerProcess()
    record.status, record.stopped_by, record.stopped_at_ms = "stopped", stopped_by, timestamp_ms()
    write_json_atomically(path, record.to_dict())


def mark_state_stopped(path: Path, *, stopped_by: str) -> None:
    state = read_run_state(path.parent)
    if state is not None and state.status in ("starting", "running", "error"):
        state.complete("stopped")
        state.stop_reason = stopped_by
        write_json_atomically(path, state.to_dict())


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
    state = read_run_state(automation_dir)
    if state is None or state.status != "starting":
        return
    state.startup_error(result.message or f"automation worker exited with code {result.exit_code}", result.exit_code)
    state.pid = os.getpid()
    if result.problems:
        state.activation_problems = result.problems
    write_json_atomically(state_path, state.to_dict())


def initialize_automation_startup(
    *,
    automation_dir: Path,
    host_name: str,
    started_at_ms: int,
    configuration: RunConfiguration,
    record: bool,
) -> None:
    state = new_run_state(
        host_name=host_name, run_id="starting", status="starting", pid=None,
        configuration=configuration, record=record, control_source=CONTROL_SOURCE,
        run_dir=None, started_at_ms=started_at_ms,
    )
    write_json_atomically(automation_dir / "state.json", state.to_dict())


def mark_automation_startup_error(
    *,
    automation_dir: Path,
    error: str,
    exit_code: int | None = None,
) -> None:
    state_path = automation_dir / "state.json"
    state = read_run_state(automation_dir) or RunState()
    state.startup_error(error, exit_code)
    write_json_atomically(state_path, state.to_dict())


def run_directory(host_name: str, *, create: bool = False) -> Path:
    directory = host_automation_dir(host_name)
    if create:
        directory.mkdir(parents=True, exist_ok=True)
    return directory


def read_process_record(automation_dir: Path) -> WorkerProcess | None:
    return WorkerProcess.from_dict(read_json(automation_dir / "process.json"))


def read_stop_count(automation_dir: Path) -> int:
    """Stops that have claimed this run. A missing record has claimed none."""

    document = read_json(automation_dir / "order.json")
    stops = document.get("stops") if isinstance(document, dict) else 0
    return stops if type(stops) is int else 0


def write_stop_count(automation_dir: Path, stops: int) -> None:
    write_json_atomically(automation_dir / "order.json", {"stops": stops})


def read_run_state(automation_dir: Path) -> RunState | None:
    return RunState.from_dict(read_json(automation_dir / "state.json"))


def record_background_worker(
    automation_dir: Path, *, host_name: str, pid: int, started_at_ms: int,
    command: list[str], log_to_disk: bool,
) -> WorkerProcess:
    """Record the launched worker before ``hosts run`` waits for its session."""

    record = WorkerProcess(
        schema="automa_automation_process", host_name=host_name, pid=pid, started_at_ms=started_at_ms,
        command=command, log_to_disk=bool(log_to_disk),
        log_path=display_path(automation_dir / "automation.log") if log_to_disk else None,
        state_path=display_path(automation_dir / "state.json"), stream_command=perception_stream_command(host_name),
    )
    write_json_atomically(automation_dir / "process.json", record.to_dict())
    return record


def record_worker_startup(
    automation_dir: Path, process_record: WorkerProcess, startup: dict[str, object],
) -> WorkerProcess:
    """Persist the startup verdict without duplicating the full run state."""

    process_record.startup = {key: value for key, value in startup.items() if key != "state"}
    write_json_atomically(automation_dir / "process.json", process_record.to_dict())
    return process_record
