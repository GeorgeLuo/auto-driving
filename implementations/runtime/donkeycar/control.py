"""Normalized command mailbox consumed by the Donkey drivetrain loop.

``DRIVE_MODES`` is the one translation between execution modes and Donkey's
drive modes. The shared runtime has no partial-autonomy mode, so every drive
mode other than ``local`` asks for manual control.
"""
from __future__ import annotations

from typing import Any

from autonomy.runtime.control import AutonomyControl
from autonomy.runtime.execution import require_mode
from implementations.vehicle.picar import PiCar

DRIVE_MODES = {"manual": "user", "observe_only": "user", "autonomy": "local"}


def execution_mode(drive_mode: str) -> str:
    """The execution mode a Donkey drive mode asks for."""
    return "autonomy" if drive_mode == DRIVE_MODES["autonomy"] else "manual"


def drive_mode(mode: str) -> str:
    """The Donkey drive mode shown while an execution mode runs."""
    return DRIVE_MODES[require_mode(mode)]


class DonkeyControlTarget:
    """In-process delivery; the drivetrain pulls ControlExecution.output()."""

    def __init__(self, car: PiCar | None = None) -> None:
        # The onboard host is the drivetrain, so it has no HTTP car.
        # Startup passes the car and uses the same acquire call as Chase.
        self.car = car

    def acquire(self) -> dict[str, Any] | None:
        if self.car is None:
            return None
        return self.car.prepare_for_external_control()

    def write(self, control: AutonomyControl) -> dict[str, Any]:
        return {"boundary": "donkey_runtime_output", "transport": "in_process"}

    def release(self) -> None:
        pass
