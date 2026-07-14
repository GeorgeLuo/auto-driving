from __future__ import annotations

import json
import unittest
from pathlib import Path

from autonomy.vehicle import (
    FRONT_CAMERA_SENSOR_ID,
    SensorReadRequest,
    SensorReading,
    SensorSnapshot,
    VehicleAction,
    VehicleCapabilities,
    VehiclePulse,
)


class VehicleActionTests(unittest.TestCase):
    def test_action_rejects_conflicting_directions_and_clamps_steering(self) -> None:
        with self.assertRaisesRegex(ValueError, "both forward and reverse"):
            VehicleAction(forward=True, reverse=True)

        self.assertEqual(VehicleAction(steering=2.5).steering, 1.0)
        self.assertEqual(VehicleAction(steering=-2.5).steering, -1.0)

    def test_action_rejects_non_finite_steering(self) -> None:
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "finite"):
                    VehicleAction(steering=value)


class VehiclePulseTests(unittest.TestCase):
    def test_pulse_normalizes_finite_execution_values(self) -> None:
        pulse = VehiclePulse(
            action=VehicleAction(forward=True, steering=0.5),
            throttle=2.0,
            duration_s=-1.0,
            settle_s=-2.0,
            recording=True,
            label="forward_check",
        )

        self.assertEqual(pulse.throttle, 1.0)
        self.assertEqual(pulse.duration_s, 0.0)
        self.assertEqual(pulse.settle_s, 0.0)
        self.assertEqual(
            pulse.to_dict(),
            {
                "action": {"forward": True, "reverse": False, "steering": 0.5},
                "throttle": 1.0,
                "duration_s": 0.0,
                "settle_s": 0.0,
                "recording": True,
                "label": "forward_check",
            },
        )

    def test_pulse_rejects_non_finite_execution_values(self) -> None:
        for field_name in ("throttle", "duration_s", "settle_s"):
            for value in (float("nan"), float("inf"), float("-inf")):
                with self.subTest(field=field_name, value=value):
                    with self.assertRaisesRegex(ValueError, "finite"):
                        VehiclePulse(**{field_name: value})


class SensorValueTests(unittest.TestCase):
    def test_sensor_request_selects_sensors_and_normalizes_capture_path(self) -> None:
        request = SensorReadRequest(
            output_dir=Path("captures"),
            read_id="frame_007",
            requested_sensors=(FRONT_CAMERA_SENSOR_ID,),
            image_extension=".png",
        )

        self.assertTrue(request.sensor_requested(FRONT_CAMERA_SENSOR_ID))
        self.assertFalse(request.sensor_requested("imu"))
        self.assertEqual(
            request.front_camera_path(),
            Path("captures/frame_007_front_camera.png"),
        )
        self.assertEqual(
            SensorReadRequest(
                output_dir=Path("captures"),
                image_extension="",
            ).front_camera_path().suffix,
            ".jpg",
        )

        serialized = request.to_dict()
        serialized["requested_sensors"].append("imu")
        self.assertFalse(request.sensor_requested("imu"))

    def test_snapshot_serialization_omits_values_and_detaches_nested_data(self) -> None:
        reading = SensorReading(
            sensor_id=FRONT_CAMERA_SENSOR_ID,
            sensor_kind="camera",
            captured_at_ms=100,
            path="captures/frame.jpg",
            value=object(),
            metadata={"capture": {"exposure": 7}},
        )
        snapshot = SensorSnapshot(
            read_id="frame_007",
            readings={FRONT_CAMERA_SENSOR_ID: reading},
            started_at_ms=90,
            completed_at_ms=110,
            request={"options": {"endpoint": "/frame.jpg"}},
            metadata={"vehicle": {"id": "test-car"}},
        )

        serialized = snapshot.to_dict()
        serialized_reading = serialized["readings"][FRONT_CAMERA_SENSOR_ID]

        self.assertNotIn("value", serialized_reading)
        self.assertTrue(serialized_reading["has_value"])
        json.dumps(serialized)

        serialized_reading["metadata"]["capture"]["exposure"] = 99
        serialized["request"]["options"]["endpoint"] = "/changed.jpg"
        serialized["metadata"]["vehicle"]["id"] = "changed-car"

        self.assertEqual(reading.metadata["capture"]["exposure"], 7)
        self.assertEqual(snapshot.request["options"]["endpoint"], "/frame.jpg")
        self.assertEqual(snapshot.metadata["vehicle"]["id"], "test-car")

    def test_capability_serialization_detaches_nested_sensor_metadata(self) -> None:
        capabilities = VehicleCapabilities(
            vehicle_id="test-car",
            vehicle_kind="test",
            sensors={"front_camera": {"formats": ["jpg"]}},
        )

        serialized = capabilities.to_dict()
        serialized["sensors"]["front_camera"]["formats"].append("png")

        self.assertEqual(
            capabilities.sensors["front_camera"]["formats"],
            ["jpg"],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
