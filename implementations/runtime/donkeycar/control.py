"""Normalized command mailbox consumed by the Donkey drivetrain loop."""
from __future__ import annotations

import threading
from typing import Any

from autonomy.runtime.control import AutonomyControl


def execution_mode(drive_mode: str) -> str:
    """Translate Donkey's wire vocabulary only; no partial-autonomy modes."""
    return {"user": "manual", "local": "autonomy", "autonomy": "autonomy",
            "observe_only": "observe_only"}.get(drive_mode, "manual")


class DonkeyControlTarget:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._control = AutonomyControl()

    def acquire(self) -> None:
        pass

    def write(self, control: AutonomyControl) -> dict[str, Any]:
        with self._lock:
            self._control = control
        return {"boundary": "donkey_drivetrain_input", "transport": "in_process"}

    def release(self) -> None:
        pass

    def read(self) -> AutonomyControl:
        with self._lock:
            return self._control
