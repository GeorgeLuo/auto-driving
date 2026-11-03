"""The run record every host's run shares.

Every runtime host executes the decision cycle while the CLI monitors it
(``monitor``), writing this ``state.json`` record. A recording also appends
the same ``manifest.json``, which replay loads. The lines a terminal prints
come from ``render``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from autonomy.runtime.session import RunConfiguration
from ...shared.paths import display_path
from ...shared.io.json_files import read_json, write_json_atomically
from ...shared.timestamps import timestamp_ms

RUN_STATE_SCHEMA = "automa_automation_run_state"
READINESS_SCHEMA = "automa_cli_readiness"


def control_source(configuration: RunConfiguration) -> str:
    return "runtime_host"


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
    automation_dir: Path,
    front_camera_path: Path,
    run_dir: Path | None,
    published_view: dict[str, Any],
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


def record_host_status(state: dict[str, Any], host_status: dict[str, Any]) -> None:
    """Record the steps and selections the host applied, each selection's report, and its decision.

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


def finish_run(
    state: dict[str, Any],
    view_state: dict[str, Any],
    *,
    status: str,
    stop_reason: str | None = None,
    error: str | None = None,
    error_code: str | None = None,
    error_details: dict[str, Any] | None = None,
    blocking_layer: str = "automation_worker",
) -> None:
    """Record how the run ended after its viewer has closed."""

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
    state["published_view"] = view_state


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
        "readiness": stopped_readiness(),
    }
    write_json_atomically(path, updated)
