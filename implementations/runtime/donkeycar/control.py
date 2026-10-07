"""Normalized command mailbox consumed by the Donkey drivetrain loop."""
from __future__ import annotations

from typing import Any

from autonomy.runtime.control import AutonomyControl


def execution_mode(drive_mode: str) -> str:
    """Translate Donkey's wire vocabulary only; no partial-autonomy modes."""
    return {"user": "manual", "local": "autonomy", "autonomy": "autonomy",
            "observe_only": "observe_only"}.get(drive_mode, "manual")


class DonkeyControlTarget:
    """In-process delivery; the drivetrain pulls ControlExecution.output()."""

    def acquire(self) -> None:
        pass

    def write(self, control: AutonomyControl) -> dict[str, Any]:
        return {"boundary": "donkey_runtime_output", "transport": "in_process"}

    def release(self) -> None:
        pass
