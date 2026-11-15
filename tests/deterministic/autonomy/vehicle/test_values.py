from __future__ import annotations

import json
import unittest

from autonomy.vehicle import (
    FRONT_CAMERA_SENSOR_ID,
    SensorReading,
    SensorFrame,
    VehicleCapabilities,
)


class SensorValueTests(unittest.TestCase):
    def test_sensor_frame_serialization_omits_values_and_includes_nested_data(self) -> None:
        reading = SensorReading(
            sensor_id=FRONT_CAMERA_SENSOR_ID,
            sensor_kind="camera",
            captured_at_ms=100,
            path="captures/frame.jpg",
            value=object(),
            metadata={"capture": {"exposure": 7}},
        )
        sensor_frame = SensorFrame(
            read_id="frame_007",
            readings={FRONT_CAMERA_SENSOR_ID: reading},
            started_at_ms=90,
            completed_at_ms=110,
            request={"options": {"endpoint": "/frame.jpg"}},
            metadata={"vehicle": {"id": "test-car"}},
        )

        serialized = sensor_frame.to_dict()
        serialized_reading = serialized["readings"][FRONT_CAMERA_SENSOR_ID]

        self.assertNotIn("value", serialized_reading)
        self.assertTrue(serialized_reading["has_value"])
        json.dumps(serialized)

        self.assertEqual(serialized_reading["metadata"], {"capture": {"exposure": 7}})
        self.assertEqual(serialized["request"], {"options": {"endpoint": "/frame.jpg"}})
        self.assertEqual(serialized["metadata"], {"vehicle": {"id": "test-car"}})

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
