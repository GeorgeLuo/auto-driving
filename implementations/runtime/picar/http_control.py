"""Normalized commands delivered to a remote PiCar through Donkey HTTP."""
from __future__ import annotations

from typing import Any

from autonomy.runtime.control import AutonomyControl
from autonomy.vehicle import VehicleAction
from implementations.vehicle.picar import PiCar


class PiCarHttpControlTarget:
    """HTTP delivery; each write sets the car's external drive input."""

    def __init__(self, car: PiCar) -> None:
        self.car = car

    def acquire(self) -> dict[str, Any]:
        return self.car.prepare_for_external_control()

    def write(self, control: AutonomyControl) -> dict[str, Any]:
        return self.car.execute_action(
            VehicleAction(
                forward=control.throttle > 0,
                reverse=control.throttle < 0,
                steering=control.steering,
            ),
            throttle=abs(control.throttle),
        )

    def release(self) -> None:
        # HTTP requests hold no transport resource; the caller owns stop policy.
        pass
