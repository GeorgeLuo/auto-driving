from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from autonomy.perception import build_perception_request
from autonomy.perception.mappers import PluginPerceptionMapper
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorReading, SensorSnapshot


PLUGIN_SPEC = (
    "lab.plugins.perception.floor_continuity.src.plugin:FloorContinuityPlugin"
)


class FloorContinuityCandidateTests(unittest.TestCase):
    def test_clear_floor_has_support_without_boundary(self) -> None:
        result = self._perceive(_scene())

        self.assertEqual(result.status, "ok")
        self.assertTrue(_signal(result, "floor_visible").value)
        self.assertFalse(_signal(result, "floor_boundary_available").value)
        self.assertEqual(
            [thing.kind for thing in result.things if thing.kind == "floor_boundary"],
            [],
        )
        self.assertEqual(result.artifacts, {})

    def test_similar_color_interruption_is_found_from_its_boundary(self) -> None:
        rgb = _scene(obstacle_center_x=80)

        result = self._perceive(rgb)

        boundaries = [thing for thing in result.things if thing.kind == "floor_boundary"]
        self.assertTrue(_signal(result, "floor_visible").value)
        self.assertTrue(_signal(result, "floor_boundary_available").value)
        self.assertTrue(any("center" in thing.location.zone for thing in boundaries))
        self.assertTrue(
            all(
                thing.properties["evidence"]
                == "multi_cue_floor_continuity_interruption"
                for thing in boundaries
            )
        )

        rejected = _mapper(minimum_boundary_confidence=1.0).perceive(
            _request(rgb, "strict-confidence")
        )
        self.assertFalse(_signal(rejected, "floor_boundary_available").value)

    def test_current_frame_removal_does_not_preserve_old_boundary(self) -> None:
        mapper = _mapper()
        first = mapper.perceive(_request(_scene(obstacle_center_x=80), "obstructed"))
        second = mapper.perceive(_request(_scene(), "clear"))

        self.assertTrue(_signal(first, "floor_boundary_available").value)
        self.assertFalse(_signal(second, "floor_boundary_available").value)

    def test_diagnostics_are_opt_in_and_frame_matched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = _mapper().perceive(
                build_perception_request(
                    _snapshot(_scene(obstacle_center_x=55), "diagnostic-frame"),
                    output_dir=Path(tmp),
                )
            )

            self.assertEqual(len(result.artifacts), 4)
            summary_path = Path(
                result.artifacts["floor-continuity-v1/summary"]
            )
            self.assertIn('"frame_id": "diagnostic-frame"', summary_path.read_text())

    def _perceive(self, rgb: np.ndarray):
        return _mapper().perceive(_request(rgb, "test-frame"))


def _mapper(**config_overrides) -> PluginPerceptionMapper:
    config = {
        "working_width": 160,
        "horizon_ratio": 0.35,
        "minimum_floor_support_px": 4,
        "minimum_interruption_run_px": 4,
        "minimum_boundary_width_ratio": 0.025,
    }
    config.update(config_overrides)
    return PluginPerceptionMapper(
        plugins=["floor_continuity"],
        plugin_specs={"floor_continuity": PLUGIN_SPEC},
        plugin_configs={"floor_continuity": config},
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


def _scene(obstacle_center_x: int | None = None) -> np.ndarray:
    height, width = 120, 160
    rgb = np.full((height, width, 3), (205, 199, 185), dtype=np.uint8)
    for y in range(42, height, 12):
        cv2.line(rgb, (0, y), (width - 1, y), (190, 185, 173), 1)
    for x in range(0, width, 20):
        cv2.line(rgb, (x, 42), (x, height - 1), (194, 189, 176), 1)
    if obstacle_center_x is not None:
        x0, x1 = obstacle_center_x - 20, obstacle_center_x + 20
        cv2.rectangle(rgb, (x0, 52), (x1, 82), (62, 67, 72), 2)
        cv2.rectangle(rgb, (x0 + 2, 54), (x1 - 2, 80), (207, 201, 188), -1)
    return rgb


def _signal(result, signal_id: str):
    return next(signal for signal in result.signals if signal.signal_id == signal_id)


if __name__ == "__main__":
    unittest.main(verbosity=2)
