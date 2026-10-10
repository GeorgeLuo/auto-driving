from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from autonomy.decision_cycle.activation import read_step_activation
from autonomy.decision_cycle.perception.interface import PERCEPTION_TEXT_SCHEMA
from autonomy.runtime.client import RuntimeClient
from autonomy.runtime.session import DEFAULT_INTERVAL_S, RunConfiguration
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID

from .vehicle_access import create_vehicle_access
from .staged_bundle import write_json_atomically
from .bundles import controller_bundle_paths
from .paths import display_path, safe_path_part
from . import runtime_hosts
from .runtime_hosts import (
    bundle_paths,
    RuntimeHostError,
    automation_dir as vehicle_automation_dir,
    running_base_url,
    runtime_base_url,
    staged_vehicle,
    stop_chase_host,
)
from .runtime_monitor import monitor_runtime
from .step_activations import (
    bundle_activation_path,
    bundle_activation_problems,
    decision_identity,
    format_activation_problems,
)
from .run_record import (
    control_application,
    control_source,
    decision_record,
    failed_readiness,
    new_run_state,
    perception_record,
    stopped_readiness,
)
from .view_discovery import discover_runtime_view
from .perception_view import (
    get_perception_view_status,
    perception_view_ready,
)
from .vehicles import DEFAULT_READINESS_TIMEOUT_S, is_chase_vehicle_id


ROOT = Path(__file__).resolve().parents[2]
AUTOMA_EXECUTABLE = ROOT / "cli" / "automa"
MAX_STATUS_REASON_CHARS = 240


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    message: str


def _host_runtime_status(
    vehicle_id: str,
    base_url: str,
    *,
    timeout_s: float,
) -> dict[str, Any]:
    """A runtime host's ``/autonomy/status`` in the worker status rows.

    While a monitor runs, its state record holds these rows; otherwise the
    host reports the same cycle counters from its observation status
    provider. The local view is the one a terminal ``vehicles stream`` hosts.
    """

    endpoint = f"{base_url}/autonomy/status"
    try:
        status = RuntimeClient(base_url, timeout_s=max(0.1, timeout_s)).host_status()
        error = None
    except RuntimeError as exc:
        status, error = {}, str(exc)
    autonomy = status.get("autonomy") if isinstance(status.get("autonomy"), dict) else {}
    components = autonomy.get("components") if isinstance(autonomy.get("components"), dict) else {}
    observation = (
        components.get("observation") if isinstance(components.get("observation"), dict) else {}
    )
    latest = observation.get("latest") if isinstance(observation.get("latest"), dict) else {}
    completed_at_ms = _int_or_none(latest.get("completed_at_ms"))
    if error is None and not autonomy:
        error = f"{base_url} answers but reports no runtime host"
    session = autonomy.get("session") or {}
    worker_status = str(session.get("status") or "stopped") if error is None else "error"
    view = discover_runtime_view(
        vehicle_id,
        lambda directory: get_perception_view_status(
            directory, timeout_s=min(0.25, max(0.0, timeout_s)),
        ),
        runtime_root=runtime_hosts.RUNTIME_ROOT,
    )
    if not view.get("available"):
        view = {
            **view,
            "reason": f"start `./cli/automa vehicles stream perception --id {vehicle_id}` to host it",
        }
    return {
        "process": {
            "pid": None,
            "running": worker_status == "running",
            "pid_state": f"host {base_url}",
            "status": worker_status,
            "generation_matches": True,
            "reason": error,
            "recovery": (
                f"./cli/automa vehicles update autonomy --id {vehicle_id} --restart"
                if error is not None
                else None
            ),
            "log_to_disk": False,
            "log_path": None,
            "command": None,
            "process_record": endpoint,
        },
        "state": {
            "status": worker_status,
            "run_id": None,
            "frames_captured": observation.get("frames_captured", 0),
            "processed_count": observation.get("processed_count", 0),
            "skipped_count": observation.get("skipped_count", 0),
            "num_decisions": (autonomy.get("session") or {}).get("configuration", {}).get(
                "num_decisions", 0
            ),
            "interval_s": observation.get("interval_s"),
            "recording": False,
            "control_source": "runtime_host",
            "action_policy": (autonomy.get("execution") or {}).get("mode"),
            "execution": autonomy.get("execution"),
            "session": autonomy.get("session"),
            "error": error,
            "last_frame": (
                {
                    "frame_id": latest.get("frame_id"),
                    "perception_completed_at_ms": completed_at_ms,
                    "cycle_duration_ms": latest.get("duration_ms"),
                }
                if latest
                else {}
            ),
            "latest_perception_text": endpoint,
            "state_record": endpoint,
            "latest_perception_age_ms": (
                None if completed_at_ms is None else max(0, _timestamp_ms() - completed_at_ms)
            ),
        },
        "published_view": view,
        "applied_decision": autonomy.get("applied_decision"),
    }


def _decision_summary(identity: Any) -> dict[str, Any]:
    """Whether a decision identity is deployed, its generation, and each step's plugins."""

    if not isinstance(identity, dict) or not isinstance(identity.get("steps"), dict):
        return {"deployed": False}
    return {
        "deployed": True,
        "generation_id": identity.get("generation_id"),
        "plugins": {
            step: (payload or {}).get("plugins") or []
            for step, payload in identity["steps"].items()
        },
    }


def run_vehicle_automation(
    *,
    vehicle_id: str,
    timeout_s: float = DEFAULT_READINESS_TIMEOUT_S,
    interval_s: float = DEFAULT_INTERVAL_S,
    num_decisions: int = 0,
    take_control: bool = True,
    record: bool = False,
    verbose: bool = False,
    output: TextIO | None = None,
) -> CommandResult:
    try:
        configuration = RunConfiguration(
            mode="autonomy" if take_control else "observe_only",
            interval_s=interval_s,
            num_decisions=num_decisions,
        )
    except (TypeError, ValueError) as exc:
        return CommandResult(2, str(exc))
    # Every vehicle runs its staged steps on its runtime host: a PiCar's Donkey
    # service or a local Chase host, each from its controller release.
    bundle = bundle_paths(vehicle_id)
    manifest_path = Path(bundle["perception_runtime_dir"]) / "active.json"
    if not manifest_path.exists():
        return CommandResult(
            2,
            "\n".join(
                [
                    f"No active perception preset found for {vehicle_id!r}.",
                    f"Expected activation: {display_path(manifest_path)}",
                    f"Run: ./cli/automa vehicles update perception --id {vehicle_id}",
                ]
            ),
        )
    problems = bundle_activation_problems(bundle, vehicle_id)
    if problems:
        return CommandResult(2, format_activation_problems(problems))
    try:
        perception_activation = read_step_activation(manifest_path, "perception")
        identity = decision_identity(bundle)
    except (FileNotFoundError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return CommandResult(2, f"Could not read staged step activations for {vehicle_id}: {exc}")
    try:
        base_url = runtime_base_url(vehicle_id, start=True)
    except RuntimeHostError as exc:
        return CommandResult(2, str(exc))
    code, message = monitor_runtime(
        vehicle_id=vehicle_id, base_url=base_url,
        automation_dir=Path(bundle["runtime_dir"]) / "automation",
        perception=perception_record(manifest_path, perception_activation),
        decision=decision_record(identity),
        step_activations={
            step: bundle_activation_path(bundle, step) for step in ("memory", "proposal")
        },
        configuration=configuration, timeout_s=timeout_s, record=record,
        verbose=verbose, output=output,
    )
    return CommandResult(code, message)


def start_vehicle_automation_background(
    *,
    vehicle_id: str,
    timeout_s: float = DEFAULT_READINESS_TIMEOUT_S,
    interval_s: float = DEFAULT_INTERVAL_S,
    num_decisions: int = 0,
    take_control: bool = True,
    record: bool = False,
    verbose: bool = False,
    log_to_disk: bool = False,
    open_view: bool = False,
    startup_wait_s: float = 20.0,
) -> CommandResult:
    try:
        configuration = RunConfiguration(
            mode="autonomy" if take_control else "observe_only",
            interval_s=interval_s,
            num_decisions=num_decisions,
        )
    except (TypeError, ValueError) as exc:
        return CommandResult(2, str(exc))
    automation_dir = vehicle_automation_dir(vehicle_id)
    automation_dir.mkdir(parents=True, exist_ok=True)
    process_path = automation_dir / "process.json"
    log_path = automation_dir / "automation.log"

    bundle = bundle_paths(vehicle_id)
    problems = bundle_activation_problems(bundle, vehicle_id)
    if problems:
        return CommandResult(2, format_activation_problems(problems))

    existing = _read_json(process_path)
    existing_pid = existing.get("pid") if isinstance(existing, dict) else None
    if isinstance(existing_pid, int) and _pid_alive(existing_pid):
        existing_state = _read_json(automation_dir / "state.json")
        existing_status = (
            existing_state.get("status") if isinstance(existing_state, dict) else "unknown"
        )
        if existing_status in {"launching", "starting"}:
            return CommandResult(
                2,
                "\n".join(
                    [
                        f"Automation is still starting for {vehicle_id}.",
                        f"PID: {existing_pid}",
                        f"State: {display_path(automation_dir / 'state.json')}",
                        "Not ready for: inspect perception",
                    ]
                ),
            )
        existing_run_id = (
            existing_state.get("run_id")
            if isinstance(existing_state, dict)
            and isinstance(existing_state.get("run_id"), str)
            else None
        )
        existing_state_pid = (
            existing_state.get("pid")
            if isinstance(existing_state, dict)
            and isinstance(existing_state.get("pid"), int)
            else None
        )
        if existing_state_pid is not None and existing_state_pid != existing_pid:
            return CommandResult(
                2,
                "\n".join(
                    [
                        f"Automation records disagree for {vehicle_id}.",
                        f"Process PID: {existing_pid}",
                        f"State PID: {existing_state_pid}",
                        "Reason: worker generation is stale; no duplicate worker was started.",
                        "Not ready for: inspect perception",
                    ]
                ),
            )
        expected_authority = {
            "action_policy": configuration.mode,
            "control_application": control_application(configuration),
        }
        actual_authority = {
            key: existing_state.get(key) if isinstance(existing_state, dict) else None
            for key in expected_authority
        }
        if actual_authority != expected_authority:
            return CommandResult(
                2,
                "\n".join(
                    [
                        f"Automation is already running for {vehicle_id}, but its authority does not match this command.",
                        f"PID: {existing_pid}",
                        (
                            "Expected authority: "
                            f"action={expected_authority['action_policy']}, "
                            f"control={expected_authority['control_application']}"
                        ),
                        (
                            "Current authority: "
                            f"action={actual_authority['action_policy'] or 'unknown'}, "
                            f"control={actual_authority['control_application'] or 'unknown'}"
                        ),
                        (
                            "Recovery: ./cli/automa vehicles automation stop "
                            f"--id {vehicle_id}"
                        ),
                        "Not ready for: inspect perception",
                    ]
                ),
            )
        view = get_perception_view_status(
            automation_dir,
            expected_run_id=existing_run_id,
            expected_worker_pid=existing_pid,
        )
        if not _view_ready_for_inspection(view):
            return CommandResult(
                2,
                "\n".join(
                    [
                        f"Automation is running for {vehicle_id}, but its perception view is not ready.",
                        f"PID: {existing_pid}",
                        f"Reason: {view.get('reason') or 'no correlated publication'}",
                        f"State: {display_path(automation_dir / 'state.json')}",
                        "Not ready for: inspect perception",
                    ]
                ),
            )
        browser_line = _open_view_message(str(view["url"])) if open_view else None
        return CommandResult(
            0,
            "\n".join(
                [
                    f"Automation already running for {vehicle_id}.",
                    f"PID: {existing_pid}",
                    f"Runtime view: {view['url']}",
                    *([browser_line] if browser_line else []),
                    "Ready for: inspect perception and stop automation",
                    f"State: {display_path(automation_dir / 'state.json')}",
                    _log_status_line(existing, log_path),
                    f"Stream: ./cli/automa vehicles stream perception --id {vehicle_id}",
                ]
            ),
        )

    command = [
        sys.executable,
        str(AUTOMA_EXECUTABLE),
        "vehicles",
        "automation",
        "run",
        "--id",
        vehicle_id,
        "--timeout-s",
        str(timeout_s),
        "--interval-s",
        str(interval_s),
        "--num-decisions",
        str(configuration.num_decisions),
        "--foreground",
    ]
    if not take_control:
        command.append("--observe-only")
    if record:
        command.append("--record")
    if verbose:
        command.append("--verbose")

    started_at_ms = _timestamp_ms()
    _initialize_automation_startup(
        automation_dir=automation_dir,
        vehicle_id=vehicle_id,
        started_at_ms=started_at_ms,
        configuration=configuration,
        record=record,
    )

    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    stdout_target: Any
    log_handle = None
    if log_to_disk:
        log_handle = log_path.open("a", encoding="utf-8")
        stdout_target = log_handle
    else:
        stdout_target = subprocess.DEVNULL
    try:
        try:
            process = subprocess.Popen(
                command,
                cwd=ROOT,
                stdout=stdout_target,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                env=env,
                text=True,
            )
        except OSError as exc:
            _mark_automation_startup_error(
                automation_dir=automation_dir,
                error=f"Could not launch automation worker: {type(exc).__name__}: {exc}",
            )
            return CommandResult(
                2,
                "\n".join(
                    [
                        f"Could not launch automation for {vehicle_id}: {type(exc).__name__}: {exc}",
                        "Not ready for: inspect perception",
                    ]
                ),
            )
    finally:
        if log_handle is not None:
            log_handle.close()

    process_record = {
        "schema": "automa_automation_process_v0",
        "vehicle_id": vehicle_id,
        "pid": process.pid,
        "started_at_ms": started_at_ms,
        "command": command,
        "log_to_disk": bool(log_to_disk),
        "log_path": display_path(log_path) if log_to_disk else None,
        "state_path": display_path(automation_dir / "state.json"),
        "latest_perception_text": display_path(automation_dir / "latest_perception.txt"),
        "stream_command": f"./cli/automa vehicles stream perception --id {vehicle_id}",
    }
    _write_json(process_path, process_record)
    startup = _wait_for_automation_startup(
        process=process,
        automation_dir=automation_dir,
        timeout_s=startup_wait_s,
    )
    process_record["startup"] = {
        key: value for key, value in startup.items() if key != "state"
    }
    _write_json(process_path, process_record)

    startup_status = startup["status"]
    if startup_status == "ready":
        phases = startup.get("phases") if isinstance(startup.get("phases"), dict) else {}
        capture_phase = (
            phases.get("capture") if isinstance(phases.get("capture"), dict) else {}
        )
        perception_phase = (
            phases.get("perception")
            if isinstance(phases.get("perception"), dict)
            else {}
        )
        lines = [
            f"Automation ready for {vehicle_id}.",
            f"PID: {process.pid}",
            f"First frame: {startup.get('frame_id', 'captured')}",
            (
                "Startup phases: "
                f"capture={capture_phase.get('duration_ms', 'unknown')}ms, "
                f"perception={perception_phase.get('duration_ms', 'unknown')}ms, "
                "view=current-generation correlated"
            ),
            f"Runtime view: {startup.get('view_url')}",
        ]
        capture_lag = startup.get("capture_lag")
        if isinstance(capture_lag, int) and capture_lag > 0:
            lines.append(f"Capture lag: {capture_lag} frames")
        lines.append("Ready for: inspect perception and stop automation")
        if open_view:
            lines.append(_open_view_message(str(startup.get("view_url"))))
        exit_code = 0
    elif startup_status == "completed":
        lines = [
            f"Automation completed for {vehicle_id} during startup verification.",
            f"PID: {process.pid}",
            "Ready for: inspect stopped deployment",
        ]
        exit_code = 0
    else:
        lines = [
            f"Automation did not become ready for {vehicle_id}.",
            f"PID: {process.pid}",
            f"Reason: {startup.get('reason', 'unknown startup failure')}",
            "Not ready for: inspect perception",
        ]
        exit_code = 2
    lines.extend(
        [
            f"State: {display_path(automation_dir / 'state.json')}",
            _log_status_line(process_record, log_path),
            f"Stream: ./cli/automa vehicles stream perception --id {vehicle_id}",
            f"View: ./cli/automa vehicles info perception --id {vehicle_id}",
        ]
    )
    return CommandResult(exit_code, "\n".join(lines))


def record_vehicle_automation_terminal_result(
    *,
    vehicle_id: str,
    result: CommandResult,
) -> None:
    """Persist failures that occur before the worker creates its normal run state."""

    if result.exit_code == 0:
        return
    automation_dir = vehicle_automation_dir(vehicle_id)
    state_path = automation_dir / "state.json"
    state = _read_json(state_path)
    if not isinstance(state, dict) or state.get("status") not in {"launching", "starting"}:
        return
    completed_at_ms = _timestamp_ms()
    _write_json(
        state_path,
        {
            **state,
            "status": "error",
            "pid": os.getpid(),
            "error": result.message or f"automation worker exited with code {result.exit_code}",
            "exit_code": result.exit_code,
            "completed_at_ms": completed_at_ms,
            "updated_at_ms": completed_at_ms,
            "published_view": {
                "status": "error",
                "available": False,
                "url": None,
                "reason": "automation worker failed before the perception view started",
            },
            "readiness": failed_readiness("automation_worker"),
        },
    )


def _initialize_automation_startup(
    *,
    automation_dir: Path,
    vehicle_id: str,
    started_at_ms: int,
    configuration: RunConfiguration,
    record: bool,
) -> None:
    view_record_path = automation_dir / "perception_view.json"
    view_record_path.unlink(missing_ok=True)
    starting_text = "\n".join(
        [
            f"schema={PERCEPTION_TEXT_SCHEMA}",
            "plugin=automation-worker",
            f"status=starting vehicle_id={vehicle_id}",
            "signal id=perception_ready value=false confidence=1.000 reason=worker_starting",
        ]
    )
    (automation_dir / "latest_perception.txt").write_text(
        starting_text + "\n",
        encoding="utf-8",
    )
    _write_json(
        automation_dir / "latest_perception.json",
        {
            "schema": "automa_latest_perception_placeholder_v0",
            "vehicle_id": vehicle_id,
            "status": "starting",
            "started_at_ms": started_at_ms,
            "text": starting_text,
            "perception": {"confidence": 0.0, "things": []},
        },
    )
    _write_json(
        automation_dir / "state.json",
        new_run_state(
            vehicle_id=vehicle_id,
            run_id="starting",
            status="starting",
            pid=None,
            configuration=configuration,
            record=record,
            control_source=control_source(configuration),
            automation_dir=automation_dir,
            front_camera_path=(
                automation_dir / "latest" / "frames" / f"latest_{FRONT_CAMERA_SENSOR_ID}.jpg"
            ),
            run_dir=None,
            published_view={
                "status": "starting",
                "available": False,
                "url": None,
                "reason": "automation run is starting",
            },
            started_at_ms=started_at_ms,
        ),
    )


def _wait_for_automation_startup(
    *,
    process: subprocess.Popen[Any],
    automation_dir: Path,
    timeout_s: float,
) -> dict[str, Any]:
    state_path = automation_dir / "state.json"
    deadline = time.monotonic() + max(0.1, float(timeout_s))
    while True:
        state = _read_json(state_path)
        state = state if isinstance(state, dict) else {}
        status = state.get("status")
        published_view = (
            state.get("published_view")
            if isinstance(state.get("published_view"), dict)
            else {}
        )
        frames_captured = state.get("frames_captured")
        processed_count = state.get("processed_count")
        last_capture = (
            state.get("last_capture")
            if isinstance(state.get("last_capture"), dict)
            else {}
        )
        last_frame = (
            state.get("last_frame")
            if isinstance(state.get("last_frame"), dict)
            else {}
        )

        if (
            status == "running"
            and isinstance(frames_captured, int)
            and frames_captured > 0
            and isinstance(processed_count, int)
            and processed_count > 0
        ):
            expected_pid = state.get("pid") if isinstance(state.get("pid"), int) else None
            expected_run_id = (
                state.get("run_id") if isinstance(state.get("run_id"), str) else None
            )
            remaining = deadline - time.monotonic()
            if remaining >= 0.05:
                view = get_perception_view_status(
                    automation_dir,
                    timeout_s=min(0.25, remaining),
                    expected_run_id=expected_run_id,
                    expected_worker_pid=expected_pid,
                )
                if (
                    _view_ready_for_inspection(view)
                    and view.get("latest_perception_frame_id")
                    == last_frame.get("frame_id")
                ):
                    return {
                        "status": "ready",
                        "frame_id": last_frame.get("frame_id"),
                        "view_url": view.get("url"),
                        "capture_lag": view.get("capture_lag"),
                        "capture_lag_ms": view.get("capture_lag_ms"),
                        "phases": {
                            "capture": {
                                "status": "complete",
                                "duration_ms": last_capture.get("capture_duration_ms"),
                            },
                            "perception": {
                                "status": "complete",
                                "duration_ms": last_frame.get("perception_duration_ms"),
                            },
                            "view": {
                                "status": "complete",
                                "duration_ms": 0,
                                "run_id": expected_run_id,
                                "worker_pid": expected_pid,
                            },
                        },
                        "state": state,
                    }
        if status == "completed":
            return {"status": "completed", "state": state}
        if status in {"error", "stopped"}:
            return {
                "status": "failed",
                "reason": state.get("error") or state.get("stop_reason") or f"worker status is {status}",
                "state": state,
            }
        if (
            status == "running"
            and isinstance(frames_captured, int)
            and frames_captured > 0
            and published_view.get("status") == "error"
        ):
            return {
                "status": "failed",
                "reason": published_view.get("reason") or published_view.get("last_error") or "perception view failed",
                "state": state,
            }

        exit_code = process.poll()
        if exit_code is not None:
            state = _read_json(state_path)
            state = state if isinstance(state, dict) else {}
            reason = state.get("error")
            if not isinstance(reason, str) or not reason:
                reason = f"automation worker exited with code {exit_code} before publishing a frame"
                _mark_automation_startup_error(
                    automation_dir=automation_dir,
                    error=reason,
                    exit_code=exit_code,
                )
                state = _read_json(state_path)
            return {"status": "failed", "reason": reason, "state": state}

        if time.monotonic() >= deadline:
            return {
                "status": "failed",
                "reason": (
                    "worker is still running but did not publish one correlated "
                    "camera/perception frame and current-generation view "
                    f"within {max(0.1, float(timeout_s)):.1f}s"
                ),
                "phases": {
                    "capture": {
                        "status": "complete"
                        if isinstance(frames_captured, int) and frames_captured > 0
                        else "incomplete",
                    },
                    "perception": {
                        "status": "complete"
                        if isinstance(processed_count, int) and processed_count > 0
                        else "incomplete",
                    },
                    "view": {
                        "status": "complete"
                        if _view_ready_for_inspection(published_view)
                        else "incomplete",
                    },
                },
                "state": state,
            }
        time.sleep(0.05)


def _view_ready_for_inspection(view: dict[str, Any]) -> bool:
    return perception_view_ready(view)


def _open_view_message(url: str) -> str:
    """Attempt the explicit browser launch without weakening worker readiness."""

    try:
        opened = webbrowser.open(url, new=2)
    except (OSError, webbrowser.Error) as exc:
        opened = False
        detail = f"{type(exc).__name__}: {exc}"
    else:
        detail = "browser launcher returned false"
    if opened:
        return f"Browser opened: {url}"
    return (
        f"Warning: could not open the browser ({detail}). "
        f"Open manually: {url}"
    )


def _mark_automation_startup_error(
    *,
    automation_dir: Path,
    error: str,
    exit_code: int | None = None,
) -> None:
    state_path = automation_dir / "state.json"
    state = _read_json(state_path)
    state = state if isinstance(state, dict) else {}
    completed_at_ms = _timestamp_ms()
    _write_json(
        state_path,
        {
            **state,
            "status": "error",
            "error": error,
            "exit_code": exit_code,
            "completed_at_ms": completed_at_ms,
            "updated_at_ms": completed_at_ms,
            "published_view": {
                "status": "error",
                "available": False,
                "url": None,
                "reason": error,
            },
            "readiness": failed_readiness("automation_worker"),
        },
    )


def get_vehicle_automation_status(
    *,
    vehicle_id: str | None = None,
    json_output: bool = False,
) -> CommandResult:
    vehicles = _collect_automation_status(vehicle_id=vehicle_id)
    payload = {
        "schema": "automa_automation_status_v0",
        "generated_at_ms": _timestamp_ms(),
        "runtime_root": display_path(runtime_hosts.RUNTIME_ROOT),
        "requested_vehicle_id": vehicle_id,
        "vehicles": vehicles,
    }
    exit_code = 0
    if vehicle_id is not None and not vehicles:
        exit_code = 2
        payload["outcome"] = {
            "status": "not_found",
            "message": f"No deployed automation runtime found for {vehicle_id!r}.",
            "expected_bundle": display_path(
                runtime_hosts.RUNTIME_ROOT / safe_path_part(vehicle_id) / "bundle"
            ),
            "recovery": (
                f"./cli/automa vehicles update perception --id {vehicle_id}"
            ),
        }
    elif not vehicles:
        payload["outcome"] = {
            "status": "empty",
            "message": "No deployed automation runtimes found.",
            "expected_bundle": None,
            "recovery": (
                "./cli/automa vehicles update perception --id <vehicle_id>"
            ),
        }
    elif any(_automation_status_needs_attention(vehicle) for vehicle in vehicles):
        payload["outcome"] = {
            "status": "degraded",
            "message": "One or more automation runtimes require attention.",
            "expected_bundle": None,
            "recovery": None,
        }
    else:
        payload["outcome"] = {
            "status": "ok",
            "message": f"Found {len(vehicles)} locally deployed automation runtime(s).",
            "expected_bundle": None,
            "recovery": None,
        }
    if json_output:
        return CommandResult(exit_code, json.dumps(payload, indent=2, sort_keys=True))
    return CommandResult(exit_code, _format_automation_status(payload))


def stop_vehicle_automation(
    *,
    vehicle_id: str,
    wait_s: float = 3.0,
) -> CommandResult:
    # The host ends the session and releases control; the monitor then
    # finishes the run record.
    base_url = running_base_url(vehicle_id)
    if base_url is not None:
        try:
            RuntimeClient(base_url, timeout_s=max(1.0, wait_s)).stop()
        except RuntimeError as exc:
            if not is_chase_vehicle_id(vehicle_id):
                return CommandResult(2, f"Could not stop {vehicle_id}: {exc}")
            forced = _replace_unanswering_host(vehicle_id, wait_s=wait_s)
            if forced is not None:
                return forced
    automation_dir = vehicle_automation_dir(vehicle_id)
    process_path = automation_dir / "process.json"
    state_path = automation_dir / "state.json"
    process = _read_json(process_path)
    pid = process.get("pid") if isinstance(process, dict) else None

    if not isinstance(pid, int):
        _mark_state_stopped(state_path, stopped_by="stop_command_no_pid")
        return CommandResult(
            0,
            "\n".join(
                [
                    f"No automation PID is recorded for {vehicle_id}.",
                    f"State: {display_path(state_path)}",
                    "Ready for: inspect stopped deployment",
                ]
            ),
        )

    if not _pid_alive(pid):
        _mark_process_stopped(process_path, process, stopped_by="stop_command_already_dead")
        _mark_state_stopped(state_path, stopped_by="stop_command_already_dead")
        return CommandResult(
            0,
            "\n".join(
                [
                    f"Automation is not running for {vehicle_id}.",
                    f"Recorded PID: {pid}",
                    f"State: {display_path(state_path)}",
                    "Ready for: inspect stopped deployment",
                ]
            ),
        )
    if not _pid_matches_automation(pid, vehicle_id):
        _mark_process_stopped(process_path, process, stopped_by="stop_command_stale_pid")
        _mark_state_stopped(state_path, stopped_by="stop_command_stale_pid")
        return CommandResult(
            0,
            "\n".join(
                [
                    f"Recorded automation PID for {vehicle_id} is alive but does not match this automation command.",
                    f"PID: {pid}",
                    "The PID record was marked stale; no process was terminated.",
                    f"Process: {_process_command(pid) or 'unknown'}",
                    "Ready for: inspect stopped deployment",
                ]
            ),
        )

    _terminate_pid(pid, signal.SIGINT, process_group=False)
    deadline = time.monotonic() + max(0.0, float(wait_s))
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            _mark_process_stopped(process_path, process, stopped_by="stop_command")
            _mark_state_stopped(state_path, stopped_by="stop_command")
            return CommandResult(
                0,
                "\n".join(
                    [
                        f"Automation stopped for {vehicle_id}.",
                        f"PID: {pid}",
                        f"State: {display_path(state_path)}",
                        "Ready for: inspect stopped deployment",
                    ]
                ),
            )
        time.sleep(0.1)

    _terminate_pid(pid, signal.SIGKILL)
    forced_deadline = time.monotonic() + 1.0
    while time.monotonic() < forced_deadline:
        if not _pid_alive(pid):
            _mark_process_stopped(process_path, process, stopped_by="stop_command_forced")
            _mark_state_stopped(state_path, stopped_by="stop_command_forced")
            return CommandResult(
                0,
                "\n".join(
                    [
                        f"Automation force-stopped for {vehicle_id}.",
                        f"PID: {pid}",
                        f"State: {display_path(state_path)}",
                        "Ready for: inspect stopped deployment",
                    ]
                ),
            )
        time.sleep(0.05)

    return CommandResult(
        2,
        "\n".join(
            [
                f"Automation did not stop for {vehicle_id}.",
                f"PID: {pid}",
                f"State: {display_path(state_path)}",
                "Not ready for: inspect stopped deployment",
            ]
        ),
    )


def _replace_unanswering_host(vehicle_id: str, *, wait_s: float) -> CommandResult | None:
    """End a Chase host that does not answer; a killed one cannot release control.

    ``None`` once the host is gone with control released.
    """

    killed = stop_chase_host(vehicle_id, wait_s=wait_s)
    if not killed:
        return None
    try:
        create_vehicle_access(staged_vehicle(vehicle_id), timeout_s=max(1.0, wait_s)).car.stop()
    except Exception as exc:  # noqa: BLE001 - reported with the recovery
        return CommandResult(2, "\n".join([
            f"The Chase host for {vehicle_id} did not answer and was killed; "
            f"stopping the car failed: {type(exc).__name__}: {exc}",
            "Stop the chaser in the simulator before the next run.",
        ]))
    return None


def restart_vehicle_automation(
    *,
    vehicle_id: str,
    timeout_s: float = DEFAULT_READINESS_TIMEOUT_S,
    interval_s: float = DEFAULT_INTERVAL_S,
    num_decisions: int = 0,
    take_control: bool = True,
    record: bool = False,
    verbose: bool = False,
    log_to_disk: bool = False,
    open_view: bool = False,
    wait_s: float = 3.0,
) -> CommandResult:
    bundle = bundle_paths(vehicle_id)
    problems = bundle_activation_problems(bundle, vehicle_id)
    if problems:
        return CommandResult(2, format_activation_problems(problems))
    stop_result = stop_vehicle_automation(vehicle_id=vehicle_id, wait_s=wait_s)
    if stop_result.exit_code != 0:
        return stop_result

    # A fresh host process loads the staged specs and configs; a Chase host
    # with none running starts on the latest release with the run.
    base_url = running_base_url(vehicle_id)
    if base_url is not None:
        try:
            RuntimeClient(base_url, timeout_s=timeout_s).restart(timeout_s=max(30.0, timeout_s))
        except (RuntimeError, TimeoutError) as exc:
            return CommandResult(2, f"Could not restart {vehicle_id}: {exc}")

    start_result = start_vehicle_automation_background(
        vehicle_id=vehicle_id,
        timeout_s=timeout_s,
        interval_s=interval_s,
        num_decisions=num_decisions,
        take_control=take_control,
        record=record,
        verbose=verbose,
        log_to_disk=log_to_disk,
        open_view=open_view,
    )
    message = "\n\n".join(part for part in (stop_result.message, start_result.message) if part)
    return CommandResult(start_result.exit_code, message)


def _collect_automation_status(
    *,
    vehicle_id: str | None,
    view_timeout_s: float | None = None,
) -> list[dict[str, Any]]:
    if vehicle_id is not None:
        candidate = runtime_hosts.RUNTIME_ROOT / safe_path_part(vehicle_id)
        candidates = [candidate] if candidate.is_dir() else []
    elif runtime_hosts.RUNTIME_ROOT.exists():
        candidates = sorted(path for path in runtime_hosts.RUNTIME_ROOT.iterdir() if path.is_dir())
    else:
        candidates = []

    # One wall-clock budget for every runtime's view probes and warm-up sleeps.
    # Aggregate status without --id must not restart the full timeout per card.
    if view_timeout_s is None:
        view_budget_s = 1.0
    else:
        view_budget_s = max(0.0, float(view_timeout_s))
    view_deadline = time.monotonic() + view_budget_s

    def _remaining_view_budget() -> float:
        return max(0.0, view_deadline - time.monotonic())

    def _exhausted_view_status() -> dict[str, Any]:
        return {
            "schema": "automa_perception_view_v1",
            "available": False,
            "status": "unavailable",
            "url": None,
            "reason": (
                "command deadline was exhausted before current-generation "
                "view health could be checked"
            ),
        }

    statuses = []
    for vehicle_runtime_dir in candidates:
        vehicle_name = vehicle_runtime_dir.name
        host_url = running_base_url(vehicle_name)
        bundle = controller_bundle_paths(vehicle_runtime_dir)
        bundle_root = Path(bundle["root_dir"])
        automation_dir = Path(bundle["runtime_dir"]) / "automation"
        perception_manifest_path = Path(bundle["perception_runtime_dir"]) / "active.json"
        process_path = automation_dir / "process.json"
        state_path = automation_dir / "state.json"
        latest_perception_path = automation_dir / "latest_perception.txt"

        perception_manifest = _read_json(perception_manifest_path)
        process = _read_json(process_path)
        state = _read_json(state_path)
        process = process if isinstance(process, dict) else {}
        state = state if isinstance(state, dict) else {}

        pid = process.get("pid") if isinstance(process.get("pid"), int) else state.get("pid")
        pid_alive = _pid_alive(pid) if isinstance(pid, int) else False
        state_pid = state.get("pid") if isinstance(state.get("pid"), int) else None
        process_pid = process.get("pid") if isinstance(process.get("pid"), int) else None
        generation_matches = (
            state_pid is None
            or process_pid is None
            or state_pid == process_pid
        )
        run_id = state.get("run_id") if isinstance(state.get("run_id"), str) else None
        remaining_view_budget = _remaining_view_budget()
        if remaining_view_budget <= 0:
            published_view = _exhausted_view_status()
        else:
            first_probe_timeout_s = min(0.25, remaining_view_budget)
            published_view = get_perception_view_status(
                automation_dir,
                timeout_s=first_probe_timeout_s,
                expected_run_id=run_id,
                expected_worker_pid=pid if isinstance(pid, int) else None,
            )
        if not generation_matches:
            worker_status = "stale"
            worker_reason = (
                "process/state worker generation mismatch: "
                f"process pid={process_pid}, state pid={state_pid}"
            )
        else:
            worker_status = _worker_status(
                pid=pid,
                pid_alive=pid_alive,
                run_status=state.get("status"),
            )
            worker_reason = _worker_reason(
                worker_status=worker_status,
                pid=pid,
                state=state,
            )
            # The first capture is published before its perception result.
            # Status waits out that gap, within the command budget. A later
            # capture does not withdraw a completed result whose image remains.
            if (
                worker_status == "running"
                and pid_alive
                and not published_view.get("available")
                and published_view.get("status") in {None, "warming", "unavailable"}
                and _remaining_view_budget() > 0
            ):
                while _remaining_view_budget() > 0:
                    sleep_s = min(0.1, _remaining_view_budget())
                    if sleep_s <= 0:
                        break
                    time.sleep(sleep_s)
                    probe_timeout_s = min(0.25, _remaining_view_budget())
                    if probe_timeout_s <= 0:
                        break
                    published_view = get_perception_view_status(
                        automation_dir,
                        timeout_s=probe_timeout_s,
                        expected_run_id=run_id,
                        expected_worker_pid=pid if isinstance(pid, int) else None,
                    )
                    if published_view.get("available"):
                        break
        activation_problems = bundle_activation_problems(bundle, vehicle_name)
        worker_recovery = activation_problems[0]["command"] if activation_problems else (
            f"./cli/automa vehicles automation restart --id {vehicle_name}"
            if worker_status in {"error", "stale"}
            else None
        )
        last_frame = state.get("last_frame") if isinstance(state.get("last_frame"), dict) else {}
        completed_at_ms = _int_or_none(last_frame.get("perception_completed_at_ms"))
        generated_at_ms = _timestamp_ms()

        perception = {}
        if isinstance(perception_manifest, dict):
            metadata = perception_manifest.get("metadata")
            perception = {
                "preset": metadata.get("preset") if isinstance(metadata, dict) else None,
                "plugins": perception_manifest.get("plugins")
                if isinstance(perception_manifest.get("plugins"), list)
                else [],
            }

        # A running monitor records the decision its host applied. Otherwise the
        # staged steps are what a start deploys; unstaged ones run their built-ins.
        try:
            identity = state.get("decision") if pid_alive else None
            decision = _decision_summary(identity) if identity else _decision_summary(decision_identity(bundle))
        except (OSError, TypeError, ValueError):
            decision = {"deployed": False}

        statuses.append(
            {
                "vehicle_id": vehicle_name,
                "activation_problems": activation_problems,
                "deployed": bundle_root.exists()
                and perception_manifest_path.exists()
                and decision["deployed"]
                and not activation_problems,
                "bundle_root": display_path(bundle_root),
                "automation_runtime_exists": automation_dir.exists(),
                "automation_dir": display_path(automation_dir),
                "perception": {
                    "deployed": perception_manifest_path.exists(),
                    "activation": display_path(perception_manifest_path),
                    **perception,
                },
                "decision": {
                    "activation": display_path(Path(bundle["runtime_dir"])),
                    **decision,
                },
                "process": {
                    "pid": pid,
                    "running": pid_alive,
                    "pid_state": "alive" if pid_alive else ("not_running" if isinstance(pid, int) else "none"),
                    "status": worker_status,
                    "generation_matches": generation_matches,
                    "reason": worker_reason,
                    "recovery": worker_recovery,
                    "log_to_disk": bool(process.get("log_to_disk")),
                    "log_path": process.get("log_path") if isinstance(process.get("log_path"), str) else None,
                    "command": process.get("command") if isinstance(process.get("command"), list) else None,
                    "process_record": display_path(process_path),
                },
                "state": {
                    "status": state.get("status", "none"),
                    "run_id": state.get("run_id"),
                    "pipeline": state.get("pipeline"),
                    "frames_captured": state.get("frames_captured", 0),
                    "processed_count": state.get("processed_count", 0),
                    "skipped_count": state.get("skipped_count", 0),
                    "num_decisions": state.get("num_decisions"),
                    "interval_s": state.get("interval_s"),
                    "recording": state.get("recording"),
                    "pid": state.get("pid"),
                    "control_source": state.get("control_source"),
                    "action_policy": state.get("action_policy"),
                    "execution": state.get("execution"),
                    "arming": state.get("arming"),
                    "session": state.get("session"),
                    "control_application": state.get("control_application"),
                    "passive_capture": state.get("passive_capture")
                    if isinstance(state.get("passive_capture"), dict)
                    else None,
                    "passive_session": state.get("passive_session")
                    if isinstance(state.get("passive_session"), dict)
                    else None,
                    "readiness": state.get("readiness")
                    if isinstance(state.get("readiness"), dict)
                    else None,
                    "error": state.get("error") if isinstance(state.get("error"), str) else None,
                    "error_code": state.get("error_code")
                    if isinstance(state.get("error_code"), str)
                    else None,
                    "error_details": state.get("error_details")
                    if isinstance(state.get("error_details"), dict)
                    else None,
                    "exit_code": _int_or_none(state.get("exit_code")),
                    "stop_reason": state.get("stop_reason")
                    if isinstance(state.get("stop_reason"), str)
                    else None,
                    "updated_at_ms": state.get("updated_at_ms"),
                    "last_capture": state.get("last_capture")
                    if isinstance(state.get("last_capture"), dict)
                    else {},
                    "last_frame": last_frame,
                    "latest_perception_text": display_path(latest_perception_path),
                    "state_record": display_path(state_path),
                    "latest_perception_age_ms": None if completed_at_ms is None else max(0, generated_at_ms - completed_at_ms),
                },
                "published_view": published_view,
            }
        )
        if host_url is not None and not pid_alive:
            host = _host_runtime_status(
                vehicle_name, host_url, timeout_s=_remaining_view_budget() or 0.5
            )
            applied = host.pop("applied_decision")
            statuses[-1].update(host)
            if applied:
                statuses[-1]["decision"].update(_decision_summary(applied))
            if activation_problems:
                statuses[-1]["process"]["recovery"] = activation_problems[0]["command"]
    return statuses


def _automation_status_needs_attention(vehicle: dict[str, Any]) -> bool:
    if not vehicle.get("deployed"):
        return True
    process = vehicle.get("process")
    return isinstance(process, dict) and process.get("status") in {"error", "stale"}


def _format_automation_status(payload: dict[str, Any]) -> str:
    vehicles = payload.get("vehicles") if isinstance(payload.get("vehicles"), list) else []
    outcome = payload.get("outcome") if isinstance(payload.get("outcome"), dict) else {}
    lines = [
        "automa automation status",
        "",
        f"runtime: {payload.get('runtime_root', 'unknown')}",
        f"deployed automations: {sum(1 for item in vehicles if isinstance(item, dict) and item.get('deployed'))}",
    ]
    if not vehicles:
        lines.extend(["", str(outcome.get("message") or "No deployed automation runtimes found.")])
        expected_bundle = outcome.get("expected_bundle")
        if isinstance(expected_bundle, str) and expected_bundle:
            lines.append(f"Expected bundle: {expected_bundle}")
        recovery = outcome.get("recovery")
        if isinstance(recovery, str) and recovery:
            lines.append(f"Next: {recovery}")
        return "\n".join(lines)

    for item in vehicles:
        if not isinstance(item, dict):
            continue
        perception = item.get("perception") if isinstance(item.get("perception"), dict) else {}
        decision = item.get("decision") if isinstance(item.get("decision"), dict) else {}
        process = item.get("process") if isinstance(item.get("process"), dict) else {}
        state = item.get("state") if isinstance(item.get("state"), dict) else {}
        published_view = item.get("published_view") if isinstance(item.get("published_view"), dict) else {}
        last_frame = state.get("last_frame") if isinstance(state.get("last_frame"), dict) else {}
        lines.extend(
            [
                "",
                str(item.get("vehicle_id", "unknown")),
                f"  deployment: {'deployed' if item.get('deployed') else 'not deployed'}",
                f"  perception: {_perception_label(perception)}",
                f"  decision: {_decision_label(decision)}",
                f"  worker: {_worker_label(process, state)}",
                f"  run: {_run_label(state)}",
                f"  latest: {_latest_status_label(state, last_frame)}",
                f"  view: {_published_view_label(published_view)}",
                f"  state: {state.get('state_record', 'unknown')}",
                f"  log: {_status_log_label(process)}",
            ]
        )
        arming = state.get("arming")
        if isinstance(arming, dict) and arming.get("request_id"):
            lines.append(f"  arming: {arming['status']} (request {arming['request_id']})")
            if arming.get("error"):
                lines.append(f"  problem: {arming['error']}")
        problems = item.get("activation_problems")
        if isinstance(problems, list) and problems:
            lines.extend(f"  {line}" for line in format_activation_problems(problems).splitlines())
        reason = process.get("reason")
        if isinstance(reason, str) and reason:
            lines.append(f"  problem: {reason}")
        recovery = process.get("recovery")
        if isinstance(recovery, str) and recovery:
            lines.append(f"  next: {recovery}")
    return "\n".join(lines)


def _perception_label(perception: dict[str, Any]) -> str:
    if not perception.get("deployed"):
        return f"not deployed; expected {perception.get('activation', 'unknown')}"
    preset = perception.get("preset") or "unknown"
    plugins = ", ".join(perception.get("plugins") or []) or "no plugins"
    return f"{preset} ({plugins})"


def _decision_label(decision: dict[str, Any]) -> str:
    if not decision.get("deployed"):
        return f"not deployed; expected {decision.get('activation', 'unknown')}"
    plugins = decision.get("plugins") if isinstance(decision.get("plugins"), dict) else {}
    described = " ".join(
        f"{step}={','.join(ids) or '-'}" for step, ids in plugins.items()
    )
    return f"{described or 'unknown'} ({decision.get('generation_id') or 'no generation'})"


def _worker_label(process: dict[str, Any], state: dict[str, Any]) -> str:
    pid = process.get("pid")
    if pid is None:
        pid = state.get("pid")
    pid_text = str(pid) if isinstance(pid, int) else "none"
    return f"{process.get('status', 'unknown')}  pid={pid_text} ({process.get('pid_state', 'unknown')})"


def _worker_status(*, pid: Any, pid_alive: bool, run_status: Any) -> str:
    if run_status == "error":
        return "error"
    if pid_alive:
        return "starting" if run_status in {"launching", "starting"} else "running"
    if run_status in {"launching", "starting", "running"}:
        return "stale"
    if run_status in {"completed", "stopped"}:
        return str(run_status)
    if isinstance(pid, int):
        return "not_running"
    return "not_started"


def _worker_reason(
    *,
    worker_status: str,
    pid: Any,
    state: dict[str, Any],
) -> str | None:
    if worker_status == "stale":
        pid_text = str(pid) if isinstance(pid, int) else "none"
        return f"recorded worker PID {pid_text} is not running"
    if worker_status == "error":
        return _status_reason(
            state.get("error"),
            fallback="automation worker reported an error",
        )
    return None


def _status_reason(value: Any, *, fallback: str) -> str:
    lines = (
        [line.strip() for line in value.splitlines() if line.strip()]
        if isinstance(value, str)
        else []
    )
    reason = lines[0] if lines else fallback
    if len(reason) <= MAX_STATUS_REASON_CHARS:
        return reason
    return f"{reason[:MAX_STATUS_REASON_CHARS]}..."


def _run_label(state: dict[str, Any]) -> str:
    num_decisions = state.get("num_decisions")
    limit_text = str(num_decisions) if num_decisions else "unbounded"
    parts = [
        f"id={state.get('run_id', 'none')}",
        f"captured={state.get('frames_captured', 0)}",
        f"decisions={state.get('processed_count', 0)}/{limit_text}",
        f"skipped={state.get('skipped_count', 0)}",
        f"capture_interval_s={state.get('interval_s', 'unknown')}",
        f"recording={state.get('recording', 'unknown')}",
        f"control={state.get('control_source', 'unknown')}",
        f"action={state.get('action_policy', 'unknown')}",
    ]
    return "  ".join(parts)


def _latest_status_label(state: dict[str, Any], last_frame: dict[str, Any]) -> str:
    if not last_frame:
        return f"none  perception={state.get('latest_perception_text', 'unknown')}"
    parts = [
        f"frame={last_frame.get('frame_id', 'none')}",
        f"signals={last_frame.get('signals', 'unknown')}",
        f"things={last_frame.get('things', 'unknown')}",
        f"perception_ms={last_frame.get('perception_duration_ms', 'unknown')}",
        f"cycle_ms={last_frame.get('cycle_duration_ms', 'unknown')}",
        f"age_ms={state.get('latest_perception_age_ms', 'unknown')}",
    ]
    return "  ".join(parts)


def _published_view_label(published_view: dict[str, Any]) -> str:
    if published_view.get("available") and published_view.get("url"):
        label = str(published_view["url"])
        lag = published_view.get("capture_lag")
        if isinstance(lag, int) and lag > 0:
            lag_ms = published_view.get("capture_lag_ms")
            if isinstance(lag_ms, int):
                return f"{label}  capture_lag={lag} ({lag_ms}ms)"
            return f"{label}  capture_lag={lag}"
        return label
    return f"unavailable ({published_view.get('reason', 'automation view is not running')})"


def _status_log_label(process: dict[str, Any]) -> str:
    if not process.get("log_to_disk"):
        return "disabled"
    return process.get("log_path") or "enabled"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _pid_matches_automation(pid: int, vehicle_id: str) -> bool:
    """Return whether *pid* looks like this vehicle's automation monitor.

    When the process command cannot be read, returns True (stop path stays
    permissive). Callers that must fail closed should read the command first
    and use :func:`_automation_command_matches_vehicle` directly.
    """

    command = _process_command(pid)
    if command is None:
        return True
    return _automation_command_matches_vehicle(command, vehicle_id)


def _automation_command_matches_vehicle(command: str, vehicle_id: str) -> bool:
    """Pure check: command is an automation run for exactly *vehicle_id*.

    Requires the contiguous launcher subcommand ``vehicles automation run`` and
    an exact ``--id <vehicle_id>`` argument pair (token equality, not substring).
    """

    if not vehicle_id or not str(vehicle_id).strip():
        return False
    vehicle_key = str(vehicle_id).strip()
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    if not tokens:
        return False
    # Contiguous launcher subcommand as emitted by start_automation.
    matched_run = False
    for index in range(len(tokens) - 2):
        if tokens[index : index + 3] == ["vehicles", "automation", "run"]:
            matched_run = True
            break
    if not matched_run:
        return False
    for index, token in enumerate(tokens):
        if token == "--id":
            if index + 1 < len(tokens) and tokens[index + 1] == vehicle_key:
                return True
        elif token.startswith("--id="):
            if token[len("--id=") :] == vehicle_key:
                return True
    return False


def _process_command(pid: int) -> str | None:
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    command = result.stdout.strip()
    return command or None


def _timestamp_ms() -> int:
    return int(time.time() * 1000)


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomically(path, payload)


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _mark_process_stopped(path: Path, process: Any, *, stopped_by: str) -> None:
    record = process if isinstance(process, dict) else {}
    updated = {
        **record,
        "status": "stopped",
        "stopped_by": stopped_by,
        "stopped_at_ms": _timestamp_ms(),
    }
    _write_json(path, updated)


def _mark_state_stopped(path: Path, *, stopped_by: str) -> None:
    state = _read_json(path)
    if not isinstance(state, dict):
        return
    if state.get("status") not in ("starting", "running", "error"):
        return
    updated = {
        **state,
        "status": "stopped",
        "stop_reason": stopped_by,
        "completed_at_ms": _timestamp_ms(),
        "updated_at_ms": _timestamp_ms(),
        "readiness": stopped_readiness(),
    }
    _write_json(path, updated)


def _log_status_line(process: Any, log_path: Path) -> str:
    record = process if isinstance(process, dict) else {}
    configured_path = record.get("log_path")
    log_to_disk = bool(record.get("log_to_disk")) or isinstance(configured_path, str)
    if not log_to_disk:
        return "Log: disabled; pass --log to persist worker output"
    if isinstance(configured_path, str) and configured_path:
        return f"Log: {configured_path}"
    return f"Log: {display_path(log_path)}"


def _terminate_pid(
    pid: int,
    sig: signal.Signals,
    *,
    process_group: bool = True,
) -> None:
    try:
        if process_group:
            os.killpg(pid, sig)
        else:
            os.kill(pid, sig)
    except OSError:
        try:
            os.kill(pid, sig)
        except OSError:
            return
