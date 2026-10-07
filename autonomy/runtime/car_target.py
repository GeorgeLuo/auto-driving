"""Adapt the public car API to the shared control execution contract."""
from __future__ import annotations

from typing import Any

from autonomy.runtime.control import AutonomyControl
from autonomy.vehicle import CarInterface, VehicleAction


class CarControlTarget:
    def __init__(self, car: CarInterface) -> None:
        self.car = car

    def acquire(self) -> None:
        self.car.acquire_control()

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
        self.car.release_control()
