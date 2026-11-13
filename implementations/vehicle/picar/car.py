from __future__ import annotations

import time

from .donkey_client import DonkeyClient
from .defaults import (
    DEFAULT_LOCAL_CAR_BASE_URL,
    DEFAULT_LOCAL_CAR_ID,
    get_default_picar_base_url,
    get_default_picar_id,
)
from autonomy.vehicle import (
    FRONT_CAMERA_SENSOR_ID,
    CarInterface,
    VehicleAction,
    VehicleCapabilities,
    VehiclePulse,
    run_vehicle_pulse,
)

VEHICLE_KIND = "picar-donkey-http"


class PiCar(CarInterface):
    """PiCar/PiRacer embodiment implemented through the Donkey web server."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_LOCAL_CAR_BASE_URL,
        timeout_s: float = 5.0,
        vehicle_id: str = DEFAULT_LOCAL_CAR_ID,
    ):
        self.client = DonkeyClient(base_url=base_url, timeout_s=timeout_s)
        self._capabilities = VehicleCapabilities(
            vehicle_id=vehicle_id,
            vehicle_kind=VEHICLE_KIND,
            sensors={
                FRONT_CAMERA_SENSOR_ID: {
                    "sensor_kind": "camera",
                    "pose": "fixed_front_low",
                    "available": True,
                    "physical_limitations": (
                        "single low-mounted forward-facing camera",
                        "no side/rear view unless the vehicle moves or turns",
                    ),
                },
            },
            notes=(
                "Donkey /drive accepts normalized angle/throttle.",
                "Joystick should be disabled unless explicitly testing manual override.",
            ),
        )

    @property
    def capabilities(self) -> VehicleCapabilities:
        return self._capabilities

    @property
    def base_url(self) -> str:
        return self.client.base_url

    def _action_to_drive(self, action: VehicleAction, throttle: float) -> tuple[float, float]:
        normalized_throttle = max(0.0, min(1.0, float(throttle)))
        if action.reverse:
            return action.steering, -normalized_throttle
        if action.forward:
            return action.steering, normalized_throttle
        return action.steering, 0.0

    def prepare_for_external_control(self) -> dict[str, object]:
        """The Donkey HTTP drive is already the external input."""
        now_ms = int(time.time() * 1000)
        return {
            "boundary": "donkey_http_input",
            "switched_control_source": False,
            "started_at_ms": now_ms,
            "completed_at_ms": now_ms,
        }

    def stop(self) -> None:
        self.client.stop()

    def execute_action(
        self,
        action: VehicleAction,
        *,
        throttle: float,
        recording: bool = False,
    ) -> dict[str, object]:
        angle, signed_throttle = self._action_to_drive(action, throttle)
        self.client.set_drive(
            angle=angle,
            throttle=signed_throttle,
            drive_mode="user",
            recording=recording,
        )
        return {
            "boundary": "donkey_http_input",
            "action": action.to_dict(),
            "angle": angle,
            "throttle": signed_throttle,
            "recording": bool(recording),
            "sent_at_ms": int(time.time() * 1000),
        }

    def execute_pulse(self, pulse: VehiclePulse) -> dict[str, object]:
        return run_vehicle_pulse(self, pulse)


def create_picar(
    *,
    base_url: str | None = None,
    timeout_s: float = 5.0,
    vehicle_id: str | None = None,
) -> PiCar:
    """Create the standard local-network PiCar object without touching the network."""
    return PiCar(
        base_url=base_url or get_default_picar_base_url(),
        timeout_s=timeout_s,
        vehicle_id=vehicle_id or get_default_picar_id(),
    )


def describe_picar(car: PiCar) -> dict[str, object]:
    return {
        "base_url": car.base_url,
        "capabilities": car.capabilities.to_dict(),
    }
