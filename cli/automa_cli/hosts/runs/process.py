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

from ...frontend.perception_view import (
    get_perception_view_status,
    perception_view_ready,
)
from ...shared.io.processes import pid_alive
from ...shared.paths import ROOT
from .models import RECOGNIZED_WORKER_MODULES, WORKER_MODULE
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

    Requires ``-m`` naming a current or pre-move worker module and an exact ``--name <host_name>``
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
        tokens[index] == "-m" and tokens[index + 1] in RECOGNIZED_WORKER_MODULES
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


def wait_for_automation_startup(
    *,
    process: subprocess.Popen[Any],
    automation_dir: Path,
    timeout_s: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + max(0.1, float(timeout_s))
    while True:
        state = read_run_state(automation_dir)
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
            state.get("last_frame") if isinstance(state.get("last_frame"), dict) else {}
        )

        if (
            status == "running"
            and isinstance(frames_received, int)
            and frames_received > 0
            and isinstance(processed_count, int)
            and processed_count > 0
        ):
            expected_pid = (
                state.get("pid") if isinstance(state.get("pid"), int) else None
            )
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
                if perception_view_ready(view):
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
                "reason": state.get("error")
                or state.get("stop_reason")
                or f"worker status is {status}",
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
                "reason": published_view.get("reason")
                or published_view.get("last_error")
                or "perception view failed",
                "state": state,
            }

        exit_code = process.poll()
        if exit_code is not None:
            state = read_run_state(automation_dir)
            state = state if isinstance(state, dict) else {}
            reason = state.get("error")
            if not isinstance(reason, str) or not reason:
                reason = f"automation worker exited with code {exit_code} before publishing a frame"
                mark_automation_startup_error(
                    automation_dir=automation_dir,
                    error=reason,
                    exit_code=exit_code,
                )
                state = read_run_state(automation_dir)
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
                        if perception_view_ready(published_view)
                        else "incomplete",
                    },
                },
                "state": state,
            }
        time.sleep(0.05)
