from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import urlparse

from autonomy.decision_cycle.perception.interface import PERCEPTION_TEXT_SCHEMA
from autonomy.runtime.client import RuntimeClient
from autonomy.runtime.session import RunConfiguration
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID

from ...shared.command_result import CommandResult
from ...shared.io.json_files import read_json, write_json_atomically
from ...shared.paths import ROOT, display_path
from ...shared.io.processes import pid_alive
from ...releases.errors import RuntimeHostError
from ...releases.paths import bundle_paths
from ..runtime_hosts import (
    automation_dir as host_automation_dir,
    running_base_url,
    runtime_base_url,
    staged_host,
)
from .automation_stop import WORKER_MODULE, stop_host_automation
from .runtime_monitor import monitor_runtime
from ..step_activations import (
    bundle_activation_problems,
    decision_identity,
    format_activation_problems,
    staged_selections,
)
from ...shared.open_view import open_view_message
from .run_record import (
    control_application,
    control_source,
    decision_record,
    failed_readiness,
    new_run_state,
)
from ...shared.timestamps import timestamp_ms
from ...frontend.perception_view import (
    get_perception_view_status,
    perception_view_ready,
)
from ...vehicles.probes import DEFAULT_READINESS_TIMEOUT_S



def _normalize_host_url(value: str) -> str:
    """The host base URL, or why this value cannot name one."""

    text = value.strip()
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("--host-url must be an http or https URL with a host")
    if parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment:
        raise ValueError("--host-url must be the host base URL, not a route or query")
    return text.rstrip("/")



def _host_name_at_host(host_url: str, *, timeout_s: float) -> str:
    """The host name reported by the runtime."""

    try:
        status = RuntimeClient(host_url, timeout_s=max(0.1, timeout_s)).host_status()
    except RuntimeError as exc:
        raise RuntimeHostError(f"Could not read the host at {host_url}: {exc}") from exc
    host_name = status.get("host_name") if isinstance(status, dict) else None
    if not isinstance(host_name, str) or not host_name.strip():
        raise RuntimeHostError(f"The host at {host_url} did not report a host name.")
    return host_name



def _resolve_run_target(
    host_name: str | None, host_url: str | None, *, timeout_s: float,
) -> tuple[str, str | None]:
    """The host name to record, and the existing host URL when one was named.

    ``--name`` finds or starts the checkout's host later. ``--host-url`` is that
    host, and its status reports the host name. Passing both or neither is refused.
    """

    if (host_name is None) == (host_url is None):
        raise ValueError("Pass either --name or --host-url.")
    if host_url is None:
        assert host_name is not None
        return host_name, None
    normalized = _normalize_host_url(host_url)
    return _host_name_at_host(normalized, timeout_s=timeout_s), normalized



def _run_configuration(*, take_control: bool, num_decisions: int | None,
                       unbounded: bool = False) -> RunConfiguration:
    """Movement runs require a positive bound or an explicit unbounded opt-in."""

    # Refuse what the runtime refuses before --unbounded replaces the count.
    if num_decisions is not None and (type(num_decisions) is not int or num_decisions < 1):
        raise ValueError("--num-decisions must be a positive integer")
    if unbounded:
        num_decisions = None

    if take_control and num_decisions is None and not unbounded:
        raise ValueError(
            "A run that applies control needs --num-decisions N (N > 0); "
            "use --unbounded to run until stopped."
        )
    return RunConfiguration(
        mode="autonomy" if take_control else "observe_only",
        num_decisions=num_decisions,
    )



def run_host_automation(
    *,
    host_name: str | None = None,
    host_url: str | None = None,
    timeout_s: float = DEFAULT_READINESS_TIMEOUT_S,
    num_decisions: int | None = None,
    unbounded: bool = False,
    take_control: bool = True,
    record: bool = False,
    verbose: bool = False,
    output: TextIO | None = None,
) -> CommandResult:
    try:
        if host_name is not None:
            staged_host(host_name)
        configuration = _run_configuration(
            take_control=take_control, num_decisions=num_decisions, unbounded=unbounded,
        )
        host_name, host_url = _resolve_run_target(host_name, host_url, timeout_s=timeout_s)
    except (TypeError, ValueError, RuntimeHostError) as exc:
        return CommandResult(2, str(exc))
    # Every vehicle runs its staged steps on its runtime host: a PiCar's Donkey
    # service or a local Chase host, each from its controller release.
    # Each step runs its staged selection, else its built-in, else nothing.
    bundle = bundle_paths(host_name)
    problems = bundle_activation_problems(bundle, host_name)
    if problems:
        return CommandResult(2, format_activation_problems(problems))
    try:
        selections = staged_selections(bundle)
        identity = decision_identity(bundle)
    except (FileNotFoundError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return CommandResult(2, f"Could not read staged step activations for {host_name}: {exc}")
    if host_url is None:
        try:
            base_url = runtime_base_url(host_name, start=True)
        except RuntimeHostError as exc:
            return CommandResult(2, str(exc))
    else:
        base_url = host_url
    code, message = monitor_runtime(
        host_name=host_name, base_url=base_url,
        automation_dir=Path(bundle["runtime_dir"]) / "automation",
        selections=selections,
        decision=decision_record(identity),
        configuration=configuration, timeout_s=timeout_s, record=record,
        verbose=verbose, output=output,
    )
    return CommandResult(code, message)



def start_host_automation_background(
    *,
    host_name: str | None = None,
    host_url: str | None = None,
    timeout_s: float = DEFAULT_READINESS_TIMEOUT_S,
    num_decisions: int | None = None,
    unbounded: bool = False,
    take_control: bool = True,
    record: bool = False,
    verbose: bool = False,
    log_to_disk: bool = False,
    open_view: bool = False,
    startup_wait_s: float = 20.0,
) -> CommandResult:
    try:
        if host_name is not None:
            staged_host(host_name)
        configuration = _run_configuration(
            take_control=take_control, num_decisions=num_decisions, unbounded=unbounded,
        )
        host_name, host_url = _resolve_run_target(host_name, host_url, timeout_s=timeout_s)
    except (TypeError, ValueError, RuntimeHostError) as exc:
        return CommandResult(2, str(exc))
    automation_dir = host_automation_dir(host_name)
    automation_dir.mkdir(parents=True, exist_ok=True)
    process_path = automation_dir / "process.json"
    log_path = automation_dir / "automation.log"

    bundle = bundle_paths(host_name)
    problems = bundle_activation_problems(bundle, host_name)
    if problems:
        return CommandResult(2, format_activation_problems(problems))

    existing = read_json(process_path)
    existing_pid = existing.get("pid") if isinstance(existing, dict) else None
    if isinstance(existing_pid, int) and pid_alive(existing_pid):
        existing_state = read_json(automation_dir / "state.json")
        existing_status = (
            existing_state.get("status") if isinstance(existing_state, dict) else "unknown"
        )
        if existing_status in {"launching", "starting"}:
            return CommandResult(
                2,
                "\n".join(
                    [
                        f"The run on host {host_name} is still starting.",
                        f"PID: {existing_pid}",
                        f"State: {display_path(automation_dir / 'state.json')}",
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
                        f"The run records for host {host_name} disagree.",
                        f"Process PID: {existing_pid}",
                        f"State PID: {existing_state_pid}",
                        "Reason: worker generation is stale; no duplicate worker was started.",
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
                        f"Host {host_name} already has a run, but its authority does not match this command.",
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
                            "Recovery: ./cli/automa hosts stop "
                            f"--name {host_name}"
                        ),
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
                        f"Host {host_name} has a run, but its perception view is not ready.",
                        f"PID: {existing_pid}",
                        f"Reason: {view.get('reason') or 'no correlated publication'}",
                        f"State: {display_path(automation_dir / 'state.json')}",
                    ]
                ),
            )
        browser_line = open_view_message(str(view["url"])) if open_view else None
        return CommandResult(
            0,
            "\n".join(
                [
                    f"Host {host_name} already has a run.",
                    f"PID: {existing_pid}",
                    f"Runtime view: {view['url']}",
                    *([browser_line] if browser_line else []),
                    f"State: {display_path(automation_dir / 'state.json')}",
                    _log_status_line(existing, log_path),
                    f"Stream: ./cli/automa plugins stream --step perception --host-name {host_name}",
                ]
            ),
        )

    command = [
        sys.executable,
        "-m",
        WORKER_MODULE,
        *(["--host-url", host_url] if host_url is not None else ["--name", host_name]),
        "--timeout-s",
        str(timeout_s),
    ]
    if configuration.num_decisions is None:
        command.append("--unbounded")
    else:
        command.extend(["--num-decisions", str(configuration.num_decisions)])
    if not take_control:
        command.append("--observe-only")
    if record:
        command.append("--record")
    if verbose:
        command.append("--verbose")

    started_at_ms = timestamp_ms()
    _initialize_automation_startup(
        automation_dir=automation_dir,
        host_name=host_name,
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
                        f"Could not start a run on host {host_name}: {type(exc).__name__}: {exc}",
                    ]
                ),
            )
    finally:
        if log_handle is not None:
            log_handle.close()

    process_record = {
        "schema": "automa_automation_process",
        "host_name": host_name,
        "pid": process.pid,
        "started_at_ms": started_at_ms,
        "command": command,
        "log_to_disk": bool(log_to_disk),
        "log_path": display_path(log_path) if log_to_disk else None,
        "state_path": display_path(automation_dir / "state.json"),
        "latest_perception_text": display_path(automation_dir / "latest_perception.txt"),
        "stream_command": f"./cli/automa plugins stream --step perception --host-name {host_name}",
    }
    write_json_atomically(process_path, process_record)
    startup = _wait_for_automation_startup(
        process=process,
        automation_dir=automation_dir,
        timeout_s=startup_wait_s,
    )
    process_record["startup"] = {
        key: value for key, value in startup.items() if key != "state"
    }
    write_json_atomically(process_path, process_record)

    startup_status = startup["status"]
    if startup_status == "ready":
        phases = startup.get("phases") if isinstance(startup.get("phases"), dict) else {}
        perception_phase = (
            phases.get("perception")
            if isinstance(phases.get("perception"), dict)
            else {}
        )
        lines = [
            f"Run started on host {host_name}.",
            f"PID: {process.pid}",
            f"First frame: {startup.get('frame_id', 'captured')}",
            (
                "Startup phases: "
                "capture=complete, "
                f"perception={_duration_label(perception_phase)}, "
                "view=current-generation correlated"
            ),
            f"Runtime view: {startup.get('view_url')}",
        ]
        capture_lag = startup.get("capture_lag")
        if isinstance(capture_lag, int) and capture_lag > 0:
            lines.append(f"Capture lag: {capture_lag} frames")
        lines.append(f"Stop: ./cli/automa hosts stop --name {host_name}")
        if open_view:
            lines.append(open_view_message(str(startup.get("view_url"))))
        exit_code = 0
    elif startup_status == "completed":
        lines = [
            f"The run on host {host_name} completed during startup verification.",
            f"PID: {process.pid}",
        ]
        exit_code = 0
    else:
        lines = [
            f"The run on host {host_name} did not become ready.",
            f"PID: {process.pid}",
            f"Reason: {startup.get('reason', 'unknown startup failure')}",
        ]
        exit_code = 2
    lines.extend(
        [
            f"State: {display_path(automation_dir / 'state.json')}",
            _log_status_line(process_record, log_path),
            f"Stream: ./cli/automa plugins stream --step perception --host-name {host_name}",
            f"Status: ./cli/automa hosts status --name {host_name} --long",
        ]
    )
    return CommandResult(exit_code, "\n".join(lines))



def record_host_automation_terminal_result(
    *,
    host_name: str,
    result: CommandResult,
) -> None:
    """Persist failures that occur before the worker creates its normal run state."""

    if result.exit_code == 0:
        return
    automation_dir = host_automation_dir(host_name)
    state_path = automation_dir / "state.json"
    state = read_json(state_path)
    if not isinstance(state, dict) or state.get("status") not in {"launching", "starting"}:
        return
    completed_at_ms = timestamp_ms()
    write_json_atomically(
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
    host_name: str,
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
            f"status=starting host_name={host_name}",
            "signal id=perception_ready value=false confidence=1.000 reason=worker_starting",
        ]
    )
    (automation_dir / "latest_perception.txt").write_text(
        starting_text + "\n",
        encoding="utf-8",
    )
    write_json_atomically(
        automation_dir / "latest_perception.json",
        {
            "schema": "automa_latest_perception_placeholder",
            "host_name": host_name,
            "status": "starting",
            "started_at_ms": started_at_ms,
            "text": starting_text,
            "perception": {"confidence": 0.0, "things": []},
        },
    )
    write_json_atomically(
        automation_dir / "state.json",
        new_run_state(
            host_name=host_name,
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
                "reason": "the run is starting",
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
        state = read_json(state_path)
        state = state if isinstance(state, dict) else {}
        status = state.get("status")
        published_view = (
            state.get("published_view")
            if isinstance(state.get("published_view"), dict)
            else {}
        )
        frames_received = state.get("frames_received")
        processed_count = state.get("processed_count")
        last_frame = (
            state.get("last_frame")
            if isinstance(state.get("last_frame"), dict)
            else {}
        )

        if (
            status == "running"
            and isinstance(frames_received, int)
            and frames_received > 0
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
                # The independent perception copier can advance while the
                # status writer exports a recording batch. The view proves a
                # matched image/result in this run and worker generation.
                if _view_ready_for_inspection(view):
                    return {
                        "status": "ready",
                        "frame_id": view["latest_perception_frame_id"],
                        "view_url": view.get("url"),
                        "capture_lag": view.get("capture_lag"),
                        "capture_lag_ms": view.get("capture_lag_ms"),
                        "phases": {
                            "capture": {"status": "complete"},
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
            and isinstance(frames_received, int)
            and frames_received > 0
            and published_view.get("status") == "error"
        ):
            return {
                "status": "failed",
                "reason": published_view.get("reason") or published_view.get("last_error") or "perception view failed",
                "state": state,
            }

        exit_code = process.poll()
        if exit_code is not None:
            state = read_json(state_path)
            state = state if isinstance(state, dict) else {}
            reason = state.get("error")
            if not isinstance(reason, str) or not reason:
                reason = f"automation worker exited with code {exit_code} before publishing a frame"
                _mark_automation_startup_error(
                    automation_dir=automation_dir,
                    error=reason,
                    exit_code=exit_code,
                )
                state = read_json(state_path)
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
                        if isinstance(frames_received, int) and frames_received > 0
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



def _duration_label(phase: dict[str, Any]) -> str:
    value = phase.get("duration_ms")
    return f"{value}ms" if isinstance(value, (int, float)) else "not reported"



def _mark_automation_startup_error(
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
            "published_view": {
                "status": "error",
                "available": False,
                "url": None,
                "reason": error,
            },
            "readiness": failed_readiness("automation_worker"),
        },
    )



def restart_host_automation(
    *,
    host_name: str,
    timeout_s: float = DEFAULT_READINESS_TIMEOUT_S,
    num_decisions: int | None = None,
    unbounded: bool = False,
    take_control: bool = True,
    record: bool = False,
    verbose: bool = False,
    log_to_disk: bool = False,
    open_view: bool = False,
    wait_s: float = 3.0,
) -> CommandResult:
    try:
        _run_configuration(
            take_control=take_control, num_decisions=num_decisions, unbounded=unbounded,
        )
    except (TypeError, ValueError) as exc:
        return CommandResult(2, str(exc))
    bundle = bundle_paths(host_name)
    problems = bundle_activation_problems(bundle, host_name)
    if problems:
        return CommandResult(2, format_activation_problems(problems))
    stop_result = stop_host_automation(host_name=host_name, wait_s=wait_s)
    if stop_result.exit_code != 0:
        return stop_result

    # A fresh host process loads the staged specs and configs; a Chase host
    # with none running starts on the latest release with the run.
    base_url = running_base_url(host_name)
    if base_url is not None:
        try:
            RuntimeClient(base_url, timeout_s=timeout_s).restart(timeout_s=max(30.0, timeout_s))
        except (RuntimeError, TimeoutError) as exc:
            return CommandResult(2, f"Could not restart {host_name}: {exc}")

    start_result = start_host_automation_background(
        host_name=host_name,
        timeout_s=timeout_s,
        num_decisions=num_decisions,
        unbounded=unbounded,
        take_control=take_control,
        record=record,
        verbose=verbose,
        log_to_disk=log_to_disk,
        open_view=open_view,
    )
    message = "\n\n".join(part for part in (stop_result.message, start_result.message) if part)
    return CommandResult(start_result.exit_code, message)



def _log_status_line(process: Any, log_path: Path) -> str:
    record = process if isinstance(process, dict) else {}
    configured_path = record.get("log_path")
    log_to_disk = bool(record.get("log_to_disk")) or isinstance(configured_path, str)
    if not log_to_disk:
        return "Log: disabled; pass --log to persist worker output"
    if isinstance(configured_path, str) and configured_path:
        return f"Log: {configured_path}"
    return f"Log: {display_path(log_path)}"



def _run_worker(argv: list[str]) -> int:
    """Run in the foreground for hosts run, reporting a failure before the run starts."""

    parser = argparse.ArgumentParser(prog=WORKER_MODULE)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--name", dest="host_name")
    target.add_argument("--host-url")
    parser.add_argument("--timeout-s", type=float, required=True)
    parser.add_argument("--num-decisions", type=int)
    parser.add_argument("--unbounded", action="store_true")
    parser.add_argument("--observe-only", action="store_true")
    parser.add_argument("--record", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    result = run_host_automation(
        host_name=args.host_name,
        host_url=args.host_url,
        timeout_s=args.timeout_s,
        num_decisions=args.num_decisions,
        unbounded=args.unbounded,
        take_control=not args.observe_only,
        record=args.record,
        verbose=args.verbose,
        output=sys.stdout,
    )
    if result.message:
        print(result.message)
    if args.host_name:
        record_host_automation_terminal_result(host_name=args.host_name, result=result)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(_run_worker(sys.argv[1:]))
