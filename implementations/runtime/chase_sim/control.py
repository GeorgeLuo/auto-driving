"""Normalized commands pushed to the Chase simulator's chaser input."""
from __future__ import annotations

from typing import Any

from autonomy.runtime.control import AutonomyControl
from autonomy.vehicle import VehicleAction
from implementations.vehicle.chase_sim import ChaseSimCar


class ChaseControlTarget:
    """WebSocket delivery; each write sets the chaser input immediately."""

    def __init__(self, car: ChaseSimCar) -> None:
        self.car = car

    def acquire(self) -> None:
        self.car.prepare_for_external_control()

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
        # Keep the stopped WS input selected; handing control to the built-in
        # chaser would resume movement after an operator stop.
        pass
