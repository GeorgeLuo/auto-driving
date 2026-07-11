from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .donkey_client import DonkeyClient
from .defaults import (
    DEFAULT_LOCAL_CAR_BASE_URL,
    DEFAULT_LOCAL_CAR_ID,
    get_default_local_car_base_url,
    get_default_local_car_id,
)
from autonomy.vehicle import (
    FRONT_CAMERA_SENSOR_ID,
    CarInterface,
    SensorReadRequest,
    SensorReading,
    SensorSnapshot,
    VehicleAction,
    VehicleCapabilities,
    VehiclePulse,
)


def _timestamp_ms() -> int:
    return int(time.time() * 1000)


def _reject_unsupported_sensors(request: SensorReadRequest) -> None:
    unsupported = set(request.requested_sensors) - {FRONT_CAMERA_SENSOR_ID}
    if unsupported:
        raise ValueError(f"unsupported PiCar sensors requested: {sorted(unsupported)}")


class DonkeyPiCar(CarInterface):
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
            vehicle_kind="picar-donkey-http",
            sensors={
                FRONT_CAMERA_SENSOR_ID: {
                    "sensor_kind": "camera",
                    "pose": "fixed_front_low",
                    "available": True,
                    "default_endpoint": "/frame.jpg",
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

    def stop(self) -> None:
        self.client.stop()

    def execute_action(
        self,
        action: VehicleAction,
        *,
        throttle: float,
        recording: bool = False,
    ) -> dict[str, Any]:
        angle, signed_throttle = self._action_to_drive(action, throttle)
        self.client.set_drive(
            angle=angle,
            throttle=signed_throttle,
            drive_mode="user",
            recording=recording,
        )
        return {
            "action": action.to_dict(),
            "angle": angle,
            "throttle": signed_throttle,
            "recording": bool(recording),
            "sent_at_ms": int(time.time() * 1000),
        }

    def execute_pulse(self, pulse: VehiclePulse) -> dict[str, Any]:
        started_ms = int(time.time() * 1000)
        try:
            command = self.execute_action(
                pulse.action,
                throttle=pulse.throttle,
                recording=pulse.recording,
            )
            time.sleep(pulse.duration_s)
        finally:
            self.stop()

        if pulse.settle_s > 0:
            time.sleep(pulse.settle_s)

        return {
            "label": pulse.label,
            "pulse": pulse.to_dict(),
            "command": command,
            "started_at_ms": started_ms,
            "completed_at_ms": int(time.time() * 1000),
        }

    def read_sensors(self, request: SensorReadRequest) -> SensorSnapshot:
        _reject_unsupported_sensors(request)
        started_ms = _timestamp_ms()
        readings: dict[str, SensorReading] = {}

        if request.sensor_requested(FRONT_CAMERA_SENSOR_ID):
            capture = self.client.download_frame(
                request.front_camera_path(),
                endpoint=request.front_camera_endpoint,
            )
            readings[FRONT_CAMERA_SENSOR_ID] = SensorReading(
                sensor_id=FRONT_CAMERA_SENSOR_ID,
                sensor_kind="camera",
                path=capture.get("path"),
                captured_at_ms=int(capture.get("captured_at_ms") or _timestamp_ms()),
                metadata=capture,
            )

        return SensorSnapshot(
            read_id=request.read_id,
            readings=readings,
            started_at_ms=started_ms,
            completed_at_ms=_timestamp_ms(),
            request=request.to_dict(),
            metadata={"vehicle": self.capabilities.to_dict()},
        )


def create_local_car(
    *,
    base_url: str | None = None,
    timeout_s: float = 5.0,
    vehicle_id: str | None = None,
) -> DonkeyPiCar:
    """Create the standard local-network PiCar object without touching the network."""
    return DonkeyPiCar(
        base_url=base_url or get_default_local_car_base_url(),
        timeout_s=timeout_s,
        vehicle_id=vehicle_id or get_default_local_car_id(),
    )


def describe_local_car(car: DonkeyPiCar) -> dict[str, Any]:
    return {
        "base_url": car.base_url,
        "capabilities": car.capabilities.to_dict(),
    }
