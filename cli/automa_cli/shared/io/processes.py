"""Processes the CLI starts and later checks on."""

from __future__ import annotations

import os
import shlex
import subprocess
import threading


def pid_alive(pid: object) -> bool:
    """Whether a process has this id; one another user owns still counts."""

    if type(pid) is not int or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def reap_on_exit(process: subprocess.Popen[bytes] | subprocess.Popen[str]) -> None:
    """Wait for a child that outlives its command in a daemon thread, so it is reaped when it exits.

    Until this process ends too, an exited child it never waited for is a
    zombie, which ``pid_alive`` counts as alive. A command ends soon after it
    starts one; the viewer runs commands and stays, and without this a run it
    stops would never be seen to stop.
    """

    threading.Thread(target=process.wait, name=f"reap-{process.pid}", daemon=True).start()


def command_tokens(command: str) -> list[str] | None:
    """Shell tokens, or None for an empty or malformed command."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    return tokens or None


def command_option_matches(tokens: list[str], flag: str, value: str) -> bool:
    """Match an exact --flag value or --flag=value, preserving token boundaries."""
    if f"{flag}={value}" in tokens:
        return True
    pair = [flag, value]
    return any(tokens[index:index + 2] == pair for index in range(len(tokens) - 1))
