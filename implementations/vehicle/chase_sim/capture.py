"""Runtime capture handoff; encoded images never enter vehicle sensor contracts."""
from __future__ import annotations

import time

from dataclasses import dataclass, field
from typing import Any, Callable

from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorFrame, SensorReading


@dataclass(frozen=True)
class ChaseCameraCapture:
    frame_id: str
    frame_index: int
    simulation_epoch: str
    captured_at_ms: int
    transport: str
    load_image: Callable[[], Any] = field(repr=False, compare=False)
    jpeg_bytes: bytes | None = field(default=None, repr=False, compare=False)

    def sensor_frame(self) -> SensorFrame:
        return SensorFrame(
            read_id=self.frame_id,
            readings={FRONT_CAMERA_SENSOR_ID: SensorReading(
                sensor_id=FRONT_CAMERA_SENSOR_ID, sensor_kind="camera",
                captured_at_ms=self.captured_at_ms, value=self.load_image(),
            )},
            started_at_ms=self.captured_at_ms, completed_at_ms=int(time.time() * 1000),
            metadata={"simulator_frame_index": self.frame_index,
                      "simulation_epoch": self.simulation_epoch,
                      "camera_transport": self.transport},
        )
