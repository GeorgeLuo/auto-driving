"""Identify and terminate CLI run processes by exact worker identity."""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import sys
import time
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Literal

from autonomy.runtime.session import RunConfiguration

from ...shared.io.processes import pid_alive
from ...shared.paths import ROOT
from ...shared.text import page_url
from .models import WORKER_MODULE
from .store import mark_automation_startup_error, read_run_state


def pid_matches_automation(pid: int, host_name: str) -> bool:
    """Return whether *pid* looks like this host's automation monitor.

    When the process command cannot be read, returns True (stop path stays
    permissive). Callers that must fail closed should read the command first
    and use :func:`automation_command_matches_host` directly.
    """

    command = process_command(pid)
    if command is None:
        return True
    return automation_command_matches_host(command, host_name)


def automation_command_matches_host(command: str, host_name: str) -> bool:
    """Pure check: command is a host run for exactly *host_name*.

    Requires ``-m`` naming the worker module and an exact ``--name <host_name>``
    argument pair (token equality, not substring).
    """

    if not host_name or not str(host_name).strip():
        return False
    host_key = str(host_name).strip()
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    if not tokens:
        return False
    if not any(
        tokens[index] == "-m" and tokens[index + 1] == WORKER_MODULE
        for index in range(len(tokens) - 1)
    ):
        return False
    for index, token in enumerate(tokens):
        if token == "--name":
            if index + 1 < len(tokens) and tokens[index + 1] == host_key:
                return True
        elif token.startswith("--name="):
            if token[len("--name=") :] == host_key:
                return True
    return False


def process_command(pid: int) -> str | None:
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


def terminate_pid(
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


def worker_command(
    *,
    host_name: str,
    host_url: str | None,
    timeout_s: float,
    configuration: RunConfiguration,
    take_control: bool,
    record: bool,
    verbose: bool,
) -> list[str]:
    command = [
        sys.executable,
        "-m",
        WORKER_MODULE,
        # The host name identifies the run to stop and status, including a run on an existing host.
        "--name",
        host_name,
        *(["--host-url", host_url] if host_url is not None else []),
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

    return command


def launch_worker(
    command: list[str], *, log_path: Path | None
) -> subprocess.Popen[Any]:
    """Start one detached worker and release the parent's log descriptor."""

    target = (
        log_path.open("a", encoding="utf-8")
        if log_path
        else nullcontext(subprocess.DEVNULL)
    )
    with target as output:
        return subprocess.Popen(
            command,
            cwd=ROOT,
            stdout=output,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
            text=True,
        )


def stop_worker(pid: int, *, wait_s: float) -> Literal["stopped", "forced", "timeout"]:
    terminate_pid(pid, signal.SIGINT, process_group=False)
    if _wait_for_exit(pid, timeout_s=max(0.0, float(wait_s)), interval_s=0.1):
        return "stopped"
    terminate_pid(pid, signal.SIGKILL)
    if _wait_for_exit(pid, timeout_s=1.0, interval_s=0.05):
        return "forced"
    return "timeout"


def _wait_for_exit(pid: int, *, timeout_s: float, interval_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(interval_s)
    return False


def _state_dict(automation_dir: Path) -> dict[str, Any]:
    state = read_run_state(automation_dir)
    return state if isinstance(state, dict) else {}


def _view_url(state: dict[str, Any]) -> str | None:
    view = state.get("published_view")
    if not isinstance(view, dict):
        return None
    return page_url(view.get("url"))


def _acknowledged_run(
    state: dict[str, Any], *, pid: int, acknowledged_run_id: str | None,
) -> tuple[str | None, dict[str, Any] | None, str | None]:
    """The worker's accepted run id and session, once this pid has written them.

    A placeholder run id is not an acknowledgement. After one id is accepted,
    a different id is a mismatch. The parent does not invent an expected id.
    """

    if state.get("pid") != pid:
        return acknowledged_run_id, None, None
    run_id = state.get("run_id")
    if not isinstance(run_id, str) or run_id in {"", "starting"}:
        return acknowledged_run_id, None, None
    if acknowledged_run_id is not None and run_id != acknowledged_run_id:
        return acknowledged_run_id, None, f"run id changed from {acknowledged_run_id} to {run_id}"
    session = state.get("session")
    if not isinstance(session, dict):
        return run_id, None, None
    return run_id, session, None


def _session_status(session: dict[str, Any] | None) -> str | None:
    if not isinstance(session, dict):
        return None
    status = session.get("status")
    return status if isinstance(status, str) else None


def _session_outcome(status: str, session: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    # Runtime completion does not erase a worker's failed recording export.
    if state.get("status") == "error":
        return _failed(state.get("error") or "automation worker failed", state)
    return {
        "status": "ready" if status == "running" else "completed",
        "session_status": status,
        "processed_count": session.get("processed_decisions", 0),
        "view_url": _view_url(state),
        "state": state,
    }


def _failed(reason: str, state: dict[str, Any]) -> dict[str, Any]:
    return {"status": "failed", "reason": reason, "state": state}


def wait_for_automation_startup(
    *,
    process: subprocess.Popen[Any],
    automation_dir: Path,
    timeout_s: float,
) -> dict[str, Any]:
    """Wait until the worker's copied session is running or completed.

    The launched pid is known here. The run id arrives on the worker's state
    record. A dead worker is not running: after it exits, only a matching
    completed session is success.
    """

    budget_s = max(0.1, float(timeout_s))
    deadline = time.monotonic() + budget_s
    acknowledged_run_id: str | None = None
    last_status: str | None = None
    while True:
        if process.poll() is not None:
            state = _state_dict(automation_dir)
            return _exited_startup(
                process, state, acknowledged_run_id, automation_dir,
            )
        state = _state_dict(automation_dir)
        acknowledged_run_id, session, problem = _acknowledged_run(
            state, pid=process.pid, acknowledged_run_id=acknowledged_run_id,
        )
        status = _session_status(session)
        if status is not None:
            last_status = status
        if problem is not None:
            return _failed(problem, state)
        if status in {"running", "completed"} and session is not None:
            return _session_outcome(status, session, state)
        if status in {"stopped", "error"}:
            reason = state.get("error") if isinstance(state.get("error"), str) and state.get("error") else None
            return _failed(reason or f"runtime session is {status}", state)
        if time.monotonic() >= deadline:
            alive = "alive" if process.poll() is None else "exited"
            return _failed(
                f"runtime session is {last_status or 'missing'} and the worker is {alive} "
                f"after {budget_s:.1f}s",
                state,
            )
        time.sleep(0.05)


def _exited_startup(
    process: subprocess.Popen[Any],
    state: dict[str, Any],
    acknowledged_run_id: str | None,
    automation_dir: Path,
) -> dict[str, Any]:
    """Reread is the caller's state. Accept only a completed session for this pid."""

    _run_id, session, problem = _acknowledged_run(
        state, pid=process.pid, acknowledged_run_id=acknowledged_run_id,
    )
    status = _session_status(session)
    if problem is not None:
        return _failed(problem, state)
    if status == "completed" and session is not None:
        return _session_outcome(status, session, state)
    reason = state.get("error") if isinstance(state.get("error"), str) and state.get("error") else None
    if reason is None:
        exit_code = process.poll()
        code = exit_code if isinstance(exit_code, int) else None
        reason = (
            f"automation worker exited with code {exit_code} "
            "before the runtime session completed"
        )
        mark_automation_startup_error(
            automation_dir=automation_dir, error=reason, exit_code=code,
        )
        state = _state_dict(automation_dir)
    return _failed(reason, state)
