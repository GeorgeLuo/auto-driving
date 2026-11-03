"""Identify and terminate CLI run processes by exact worker identity."""
from __future__ import annotations
import os
import shlex
import signal
import subprocess
from .models import RECOGNIZED_WORKER_MODULES

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
