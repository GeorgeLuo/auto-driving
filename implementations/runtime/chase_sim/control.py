"""Normalized commands pushed to the Chase simulator's chaser input."""
from __future__ import annotations

import io
import threading
from typing import Any

from autonomy.runtime.control import AutonomyControl
from autonomy.vehicle import VehicleAction
from implementations.vehicle.chase_sim import ChaseSimCar

# Visually unchanged captures in a row. Past this, a forward or hold command
# is not moving the view, so Chase backs up until the picture changes.
_STILL_VIEWS = 6


def _picture(jpeg: bytes) -> bytes | None:
    """Coarse luma thumbnail. Encoder noise on a still camera stays in one bin."""

    try:
        from PIL import Image
        import numpy as np

        image = Image.open(io.BytesIO(jpeg)).convert("L").resize((32, 24))
        return (np.asarray(image, dtype=np.uint8) // 16).tobytes()
    except Exception:
        return None


class ChaseControlTarget:
    """WebSocket delivery; each write sets the chaser input immediately."""

    def __init__(self, car: ChaseSimCar) -> None:
        self.car = car
        self._lock = threading.Lock()
        self._view: bytes | None = None
        self._same_views = 0

    def acquire(self) -> dict[str, Any]:
        return self.car.prepare_for_external_control()

    def note_view(self, jpeg: bytes) -> None:
        """Record whether this capture is the same picture as the last one."""

        digest = _picture(jpeg)
        with self._lock:
            if digest is not None and digest == self._view:
                self._same_views += 1
            else:
                self._view = digest
                self._same_views = 0

    def write(self, control: AutonomyControl) -> dict[str, Any]:
        throttle = control.throttle
        steering = control.steering
        with self._lock:
            # Driving into contact, or holding, leaves the camera frozen.
            # Reverse until a new picture arrives.
            if self._same_views >= _STILL_VIEWS and throttle >= 0:
                throttle = -0.6
                if steering == 0:
                    steering = 1.0
        return self.car.execute_action(
            VehicleAction(
                forward=throttle > 0,
                reverse=throttle < 0,
                steering=steering,
            ),
            throttle=abs(throttle),
        )

    def release(self) -> None:
        # Keep the stopped WS input selected; handing control to the built-in
        # chaser would resume movement after an operator stop.
        pass
