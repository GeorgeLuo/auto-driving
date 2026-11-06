"""Processes the CLI starts and later checks on."""

from __future__ import annotations

import os
import subprocess
import threading
from typing import Any


def pid_alive(pid: Any) -> bool:
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


def reap_on_exit(process: subprocess.Popen[Any]) -> None:
    """Wait for a child that outlives its command in a daemon thread, so it is reaped when it exits.

    Until this process ends too, an exited child it never waited for is a
    zombie, which ``pid_alive`` counts as alive. A command ends soon after it
    starts one; the viewer runs commands and stays, and without this a run it
    stops would never be seen to stop.
    """

    threading.Thread(target=process.wait, name=f"reap-{process.pid}", daemon=True).start()
