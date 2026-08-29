from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from autonomy.perception import build_perception_request
from autonomy.perception.mappers import PluginPerceptionMapper
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorReading, SensorSnapshot
from lab.plugins.perception.floor_continuity_capture.src.plugin import (
    CaptureFloorContinuityPlugin,
)


PLUGIN_SPEC = (
    "lab.plugins.perception.floor_continuity_capture.src.plugin:"
    "CaptureFloorContinuityPlugin"
)


class CaptureFloorContinuityCandidateTests(unittest.TestCase):
    def test_capture_defaults_emit_bounded_boundary_evidence(self) -> None:
        rgb = np.full((120, 160, 3), (205, 199, 185), dtype=np.uint8)
        rgb[58:84, 38:78] = (62, 67, 72)

        result = _mapper().perceive(_request(rgb, "frame-19"))

        self.assertEqual(result.status, "ok")
        self.assertEqual(CaptureFloorContinuityPlugin.plugin_id, "floor-continuity-capture-v1")
        self.assertTrue(_signal(result, "floor_visible").value)
        boundaries = [thing for thing in result.things if thing.kind == "floor_boundary"]
        self.assertTrue(boundaries)
        self.assertTrue(_signal(result, "floor_boundary_available").value)
        self.assertTrue(all(thing.location.frame == "image" for thing in boundaries))
        self.assertTrue(
            all(
                thing.properties["evidence"]
                == "multi_cue_floor_continuity_interruption"
                for thing in boundaries
            )
        )

    def test_diagnostics_use_variant_id_and_overrides_remain_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = _mapper(minimum_boundary_confidence=1.0).perceive(
                build_perception_request(
                    _snapshot(_obstructed_scene(), "diagnostic-frame"),
                    output_dir=Path(tmp),
                )
            )

            self.assertFalse(_signal(result, "floor_boundary_available").value)
            summary_path = Path(
                result.artifacts["floor-continuity-capture-v1/summary"]
            )
            self.assertEqual(
                json.loads(summary_path.read_text())["plugin_id"],
                "floor-continuity-capture-v1",
            )


def _mapper(**config_overrides) -> PluginPerceptionMapper:
    return PluginPerceptionMapper(
        plugins=["floor_continuity_capture"],
        plugin_specs={"floor_continuity_capture": PLUGIN_SPEC},
        plugin_configs={"floor_continuity_capture": config_overrides},
    )


def _request(rgb: np.ndarray, frame_id: str):
    return build_perception_request(_snapshot(rgb, frame_id))


def _snapshot(rgb: np.ndarray, frame_id: str) -> SensorSnapshot:
    return SensorSnapshot(
        read_id=frame_id,
        readings={
            FRONT_CAMERA_SENSOR_ID: SensorReading(
                sensor_id=FRONT_CAMERA_SENSOR_ID,
                sensor_kind="camera",
                captured_at_ms=1,
                value=rgb,
                metadata={"color_space": "RGB"},
            )
        },
        started_at_ms=1,
        completed_at_ms=1,
    )


def _signal(result, signal_id: str):
    return next(signal for signal in result.signals if signal.signal_id == signal_id)


def _obstructed_scene() -> np.ndarray:
    rgb = np.full((120, 160, 3), (205, 199, 185), dtype=np.uint8)
    rgb[58:84, 38:78] = (62, 67, 72)
    return rgb


if __name__ == "__main__":
    unittest.main(verbosity=2)
