from __future__ import annotations

import time
from typing import Any

from autonomy.decision import DecisionFrameContext
from autonomy.runtime.cycle_host import AutonomyCycleHost
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorReading, SensorSnapshot


def timestamp_ms() -> int:
    return int(time.time() * 1000)


class AutonomyPilotPart:
    """Adapt Donkey image memory and pilot outputs to the shared cycle host."""

    def __init__(self, *, host: AutonomyCycleHost) -> None:
        self.host = host
        self.frame_index = 0
        self.last_status: dict[str, Any] = self.host.status()

    def run(
        self,
        image_array=None,
        mode: str = "user",
        user_steering: float = 0.0,
        user_throttle: float = 0.0,
    ):
        captured_at_ms = timestamp_ms()
        frame_id = f"donkey_frame_{self.frame_index:06d}"
        sensor_snapshot = SensorSnapshot(
            read_id=frame_id,
            readings={
                FRONT_CAMERA_SENSOR_ID: SensorReading(
                    sensor_id=FRONT_CAMERA_SENSOR_ID,
                    sensor_kind="camera",
                    captured_at_ms=captured_at_ms,
                    value=image_array,
                    metadata={"source": "donkeycar_vehicle_memory"},
                )
            },
            started_at_ms=captured_at_ms,
            completed_at_ms=captured_at_ms,
            metadata={"runtime": "donkeycar"},
        )
        cycle_result = self.host.run(
            DecisionFrameContext(
                frame_id=frame_id,
                frame_index=self.frame_index,
                timestamp_ms=captured_at_ms,
                sensor_snapshot=sensor_snapshot,
                mode=mode or "user",
                user_steering=float(user_steering or 0.0),
                user_throttle=float(user_throttle or 0.0),
                metadata={
                    "runtime": "donkeycar",
                    "control_application": "donkey_drive_mode",
                },
            )
        )
        self.frame_index += 1
        control = cycle_result.control
        self.last_status = self.host.status()
        return (
            control.steering,
            control.throttle,
            control.to_dict(),
            self.last_status["engine"].get("engine"),
            cycle_result.to_dict(),
        )
