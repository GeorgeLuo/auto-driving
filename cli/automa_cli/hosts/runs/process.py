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
from typing import Literal

from autonomy.runtime.session import RunConfiguration

from ...shared.io.processes import pid_alive, reap_on_exit
from ...shared.paths import ROOT
from .lifecycle import keep
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

    return command


def launch_worker(
    command: list[str], *, log_path: Path | None
) -> subprocess.Popen[str]:
    """Start one detached worker and release the parent's log descriptor."""

    target = (
        log_path.open("a", encoding="utf-8")
        if log_path
        else nullcontext(subprocess.DEVNULL)
    )
    with target as output:
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            stdout=output,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
            text=True,
        )
    reap_on_exit(process)
    return process


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


def _state_dict(automation_dir: Path) -> dict[str, object]:
    state = read_run_state(automation_dir)
    return state if isinstance(state, dict) else {}


def _failed(reason: str, state: dict[str, object]) -> dict[str, object]:
    return {"status": "failed", "reason": reason, "state": state}


def _record_startup_exit(automation_dir: Path, reason: str, exit_code: int, begun: int | None) -> None:
    """Record a worker that exited before its session was ready, unless a stop claimed the run."""

    def write() -> None:
        mark_automation_startup_error(
            automation_dir=automation_dir, error=reason, exit_code=exit_code,
        )

    if begun is None:
        write()
        return
    keep(automation_dir, begun, write)


def wait_for_automation_startup(
    *,
    process: subprocess.Popen[str],
    automation_dir: Path,
    timeout_s: float,
    begun: int | None = None,
) -> dict[str, object]:
    """Wait until the worker's runtime session is running, or the worker ends its run.

    Only the record this worker's pid wrote counts. While the worker is alive,
    a running session is ready. Once the worker records how its run ended,
    that is the outcome, and only ``completed`` succeeds. A worker that exits
    without recording an ending failed.
    """

    budget_s = max(0.1, float(timeout_s))
    deadline = time.monotonic() + budget_s
    while True:
        # Read the record after the exit check, so an exited worker's last write is seen.
        exit_code = process.poll()
        state = _state_dict(automation_dir)
        own = state.get("pid") == process.pid
        status = state.get("status") if own else None
        session = state.get("session") if own and isinstance(state.get("session"), dict) else {}
        if status == "completed":
            return {"status": "completed", "processed_count": state.get("processed_count", 0), "state": state}
        if status in {"stopped", "error"}:
            return _failed(state.get("error") or state.get("stop_reason") or f"the run {status}", state)
        if exit_code is not None:
            reason = f"automation worker exited with code {exit_code} before the runtime session completed"
            _record_startup_exit(automation_dir, reason, exit_code, begun)
            return _failed(reason, _state_dict(automation_dir))
        if session.get("status") == "running":
            return {"status": "ready", "processed_count": session.get("processed_decisions", 0), "state": state}
        if time.monotonic() >= deadline:
            return _failed(
                f"runtime session is {session.get('status') or 'missing'} and the worker is alive "
                f"after {budget_s:.1f}s",
                state,
            )
        time.sleep(0.05)
