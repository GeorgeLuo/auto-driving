from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from autonomy.perception import build_perception_request
from autonomy.perception.mappers import PluginPerceptionMapper
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorReading, SensorSnapshot
from implementations.perception.catalog import PERCEPTION_PLUGIN_SPECS
from implementations.perception.components import (
    FRONT_CAMERA_RGB_INPUT,
    camera_component_id,
    provide_camera_frame,
)


FRONT_CAMERA_COMPONENT = camera_component_id(FRONT_CAMERA_SENSOR_ID)


def _mapper(plugin_id: str) -> PluginPerceptionMapper:
    return PluginPerceptionMapper(
        plugins=[plugin_id],
        plugin_specs=PERCEPTION_PLUGIN_SPECS,
    )


def _snapshot(reading: SensorReading, read_id: str = "test-frame") -> SensorSnapshot:
    return SensorSnapshot(
        read_id=read_id,
        readings={reading.sensor_id: reading},
        started_at_ms=reading.captured_at_ms,
        completed_at_ms=reading.captured_at_ms,
    )


def _array_reading(
    rgb: np.ndarray | None = None,
    captured_at_ms: int = 10,
) -> SensorReading:
    return SensorReading(
        sensor_id=FRONT_CAMERA_SENSOR_ID,
        sensor_kind="camera",
        captured_at_ms=captured_at_ms,
        value=rgb if rgb is not None else np.zeros((8, 8, 3), dtype=np.uint8),
        metadata={"color_space": "RGB"},
    )


def _path_reading(path: Path, *, captured_at_ms: int) -> SensorReading:
    return SensorReading(
        sensor_id=FRONT_CAMERA_SENSOR_ID,
        sensor_kind="camera",
        captured_at_ms=captured_at_ms,
        path=str(path),
    )


class CameraComponentTests(unittest.TestCase):
    def test_camera_provider_normalizes_path_and_array_to_read_only_rgb(self) -> None:
        rgb = np.zeros((12, 16, 3), dtype=np.uint8)
        rgb[:, :, 0] = 220
        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "frame.png"
            Image.fromarray(rgb, mode="RGB").save(image_path)
            path_request = build_perception_request(
                _snapshot(_path_reading(image_path, captured_at_ms=10))
            )
            array_request = build_perception_request(
                _snapshot(_array_reading(rgb.copy(), captured_at_ms=11))
            )

            path_frame = provide_camera_frame(path_request, FRONT_CAMERA_RGB_INPUT)
            array_frame = provide_camera_frame(array_request, FRONT_CAMERA_RGB_INPUT)

        np.testing.assert_array_equal(path_frame.rgb, array_frame.rgb)
        self.assertFalse(path_frame.rgb.flags.writeable)
        self.assertEqual(path_frame.to_dict()["color_space"], "RGB")
        self.assertEqual(array_frame.metadata["normalized_from"], "value")

    def test_invalid_camera_is_reported_by_framework_without_invoking_plugin(self) -> None:
        mapper = _mapper("frame")
        request = build_perception_request(
            _snapshot(
                SensorReading(
                    sensor_id=FRONT_CAMERA_SENSOR_ID,
                    sensor_kind="camera",
                    captured_at_ms=10,
                    value=object(),
                )
            )
        )

        result = mapper.perceive(request)

        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.plugin_runs[0].status, "unavailable")
        self.assertIn("unsupported camera value type", result.plugin_runs[0].error or "")
        self.assertEqual(result.signals, ())
        self.assertEqual(result.things, ())

    def test_schema_is_generated_from_plugin_contracts(self) -> None:
        schema = _mapper("frame").describe_schema()

        self.assertEqual(schema["inputs"][0]["component_id"], FRONT_CAMERA_COMPONENT)
        self.assertEqual(schema["inputs"][0]["required_by"], ["frame-observation-v0"])
        self.assertEqual(schema["plugins"][0]["contract"]["inputs"][0]["name"], "frame")


if __name__ == "__main__":
    unittest.main(verbosity=2)
