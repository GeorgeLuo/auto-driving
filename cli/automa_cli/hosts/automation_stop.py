"""``automa hosts stop``: end a host's run and mark its worker's records stopped."""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

from autonomy.runtime.client import RuntimeClient

from ..shared.command_result import CommandResult
from .vehicle_access import create_vehicle_access
from ..shared.json_files import read_json, write_json_atomically
from ..shared.paths import display_path
from ..shared.processes import pid_alive
from ..shared.controller_releases import RuntimeHostError
from .runtime_hosts import (
    automation_dir as host_automation_dir,
    running_base_url,
    staged_host,
    stop_chase_host,
)
from ..shared.timestamps import timestamp_ms
from .run_record import stopped_readiness
from ..vehicles.providers import PROVIDERS


# hosts run starts this module (hosts/automation.py) as the run's worker.
WORKER_MODULE = "cli.automa_cli.hosts.automation"


def stop_host_automation(
    *,
    host_name: str,
    wait_s: float = 3.0,
) -> CommandResult:
    # The host ends the session and releases control; the monitor then
    # finishes the run record.
    try:
        base_url = running_base_url(host_name)
    except RuntimeHostError as exc:
        return CommandResult(2, str(exc))
    if base_url is not None:
        try:
            RuntimeClient(base_url, timeout_s=max(1.0, wait_s)).stop()
        except RuntimeError as exc:
            if not PROVIDERS[staged_host(host_name)["provider"]].local_host:
                return CommandResult(2, f"Could not stop {host_name}: {exc}")
            forced = _replace_unanswering_host(host_name, wait_s=wait_s)
            if forced is not None:
                return forced
    automation_dir = host_automation_dir(host_name)
    process_path = automation_dir / "process.json"
    state_path = automation_dir / "state.json"
    process = read_json(process_path)
    pid = process.get("pid") if isinstance(process, dict) else None

    if not isinstance(pid, int):
        _mark_state_stopped(state_path, stopped_by="stop_command_no_pid")
        return CommandResult(
            0,
            "\n".join(
                [
                    f"No run PID is recorded for host {host_name}.",
                    f"State: {display_path(state_path)}",
                ]
            ),
        )

    if not pid_alive(pid):
        _mark_process_stopped(process_path, process, stopped_by="stop_command_already_dead")
        _mark_state_stopped(state_path, stopped_by="stop_command_already_dead")
        return CommandResult(
            0,
            "\n".join(
                [
                    f"Host {host_name} has no run.",
                    f"Recorded PID: {pid}",
                    f"State: {display_path(state_path)}",
                ]
            ),
        )
    if not _pid_matches_automation(pid, host_name):
        _mark_process_stopped(process_path, process, stopped_by="stop_command_stale_pid")
        _mark_state_stopped(state_path, stopped_by="stop_command_stale_pid")
        return CommandResult(
            0,
            "\n".join(
                [
                    f"The recorded run PID for host {host_name} is alive but is not this host's run.",
                    f"PID: {pid}",
                    "The PID record was marked stale; no process was terminated.",
                    f"Process: {_process_command(pid) or 'unknown'}",
                ]
            ),
        )

    _terminate_pid(pid, signal.SIGINT, process_group=False)
    deadline = time.monotonic() + max(0.0, float(wait_s))
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            _mark_process_stopped(process_path, process, stopped_by="stop_command")
            _mark_state_stopped(state_path, stopped_by="stop_command")
            return CommandResult(
                0,
                "\n".join(
                    [
                        f"Stopped the run on host {host_name}.",
                        f"PID: {pid}",
                        f"State: {display_path(state_path)}",
                    ]
                ),
            )
        time.sleep(0.1)

    _terminate_pid(pid, signal.SIGKILL)
    forced_deadline = time.monotonic() + 1.0
    while time.monotonic() < forced_deadline:
        if not pid_alive(pid):
            _mark_process_stopped(process_path, process, stopped_by="stop_command_forced")
            _mark_state_stopped(state_path, stopped_by="stop_command_forced")
            return CommandResult(
                0,
                "\n".join(
                    [
                        f"Force-stopped the run on host {host_name}.",
                        f"PID: {pid}",
                        f"State: {display_path(state_path)}",
                    ]
                ),
            )
        time.sleep(0.05)

    return CommandResult(
        2,
        "\n".join(
            [
                f"The run on host {host_name} did not stop.",
                f"PID: {pid}",
                f"State: {display_path(state_path)}",
            ]
        ),
    )



def _replace_unanswering_host(host_name: str, *, wait_s: float) -> CommandResult | None:
    """End a Chase host that does not answer; a killed one cannot release control.

    ``None`` once the host is gone with control released.
    """

    killed = stop_chase_host(host_name, wait_s=wait_s)
    if not killed:
        return None
    try:
        create_vehicle_access(staged_host(host_name), timeout_s=max(1.0, wait_s)).car.stop()
    except Exception as exc:  # noqa: BLE001 - reported with the recovery
        return CommandResult(2, "\n".join([
            f"The Chase host for {host_name} did not answer and was killed; "
            f"stopping the car failed: {type(exc).__name__}: {exc}",
            "Stop the chaser in the simulator before the next run.",
        ]))
    return None



def _pid_matches_automation(pid: int, host_name: str) -> bool:
    """Return whether *pid* looks like this host's automation monitor.

    When the process command cannot be read, returns True (stop path stays
    permissive). Callers that must fail closed should read the command first
    and use :func:`_automation_command_matches_host` directly.
    """

    command = _process_command(pid)
    if command is None:
        return True
    return _automation_command_matches_host(command, host_name)



def _automation_command_matches_host(command: str, host_name: str) -> bool:
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
    if not any(tokens[index:index + 2] == ["-m", WORKER_MODULE] for index in range(len(tokens) - 1)):
        return False
    for index, token in enumerate(tokens):
        if token == "--name":
            if index + 1 < len(tokens) and tokens[index + 1] == host_key:
                return True
        elif token.startswith("--name="):
            if token[len("--name=") :] == host_key:
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



def _mark_process_stopped(path: Path, process: Any, *, stopped_by: str) -> None:
    record = process if isinstance(process, dict) else {}
    updated = {
        **record,
        "status": "stopped",
        "stopped_by": stopped_by,
        "stopped_at_ms": timestamp_ms(),
    }
    write_json_atomically(path, updated)



def _mark_state_stopped(path: Path, *, stopped_by: str) -> None:
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
