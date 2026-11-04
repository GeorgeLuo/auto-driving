"""Processes the CLI starts and later checks on."""

from __future__ import annotations

import os
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
