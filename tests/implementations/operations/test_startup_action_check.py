from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from autonomy.vehicle import (
    FRONT_CAMERA_SENSOR_ID,
    SensorReadRequest,
    SensorReading,
    SensorSnapshot,
    VehicleAction,
    VehicleCapabilities,
    VehiclePulse,
)
from implementations.operations import (
    build_basic_startup_action_check_plan,
    run_startup_action_check,
)


class FakeImageCar:
    def __init__(self) -> None:
        self.scene = 0
        self.pulses: list[str] = []
        self._capabilities = VehicleCapabilities(
            vehicle_id="fake-car",
            vehicle_kind="test-image-car",
            can_capture_highres=False,
        )

    @property
    def capabilities(self) -> VehicleCapabilities:
        return self._capabilities

    def stop(self) -> None:
        return None

    def execute_action(
        self,
        action: VehicleAction,
        *,
        throttle: float,
        recording: bool = False,
    ) -> dict[str, Any]:
        return {"action": action.to_dict(), "throttle": throttle, "recording": recording}

    def execute_pulse(self, pulse: VehiclePulse) -> dict[str, Any]:
        self.pulses.append(pulse.label)
        if pulse.throttle > 0:
            self.scene = 255 - self.scene
        return {"label": pulse.label, "pulse": pulse.to_dict()}

    def read_sensors(self, request: SensorReadRequest) -> SensorSnapshot:
        path = request.front_camera_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        image = np.full((48, 64, 3), self.scene, dtype=np.uint8)
        cv2.imwrite(str(path), image)
        reading = SensorReading(
            sensor_id=FRONT_CAMERA_SENSOR_ID,
            sensor_kind="camera",
            path=str(path),
            captured_at_ms=1,
            metadata={"fake": True, "path": str(path)},
        )
        return SensorSnapshot(
            read_id=request.read_id,
            readings={FRONT_CAMERA_SENSOR_ID: reading},
            started_at_ms=1,
            completed_at_ms=1,
            request=request.to_dict(),
        )


class StartupOperationTests(unittest.TestCase):
    def test_generic_plan_scores_still_and_motion_checks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            car = FakeImageCar()
            plan = build_basic_startup_action_check_plan(
                duration_s=0.0,
                settle_s=0.0,
            )
            out_dir = Path(tmp) / "startup"
            report = run_startup_action_check(
                car=car,
                plan=plan,
                out_dir=out_dir,
            )

            self.assertTrue(report["passed"])
            self.assertEqual(report["checks_passed"], 7)
            self.assertEqual(len(car.pulses), 7)
            self.assertTrue((out_dir / "report.json").exists())
            self.assertTrue((out_dir / "contact_sheet.jpg").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
