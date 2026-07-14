from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from autonomy.perception import build_perception_request
from autonomy.perception.mappers import PluginPerceptionMapper
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorReading, SensorSnapshot
from implementations.perception.catalog import PERCEPTION_PLUGIN_SPECS
from implementations.perception.components import camera_component_id
from implementations.perception.motion.tracks import MotionTracksPlugin


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


class PerceptionPluginTests(unittest.TestCase):
    def test_current_plugins_share_camera_component_without_writing_diagnostics(self) -> None:
        rgb = np.random.default_rng(3).integers(0, 256, (72, 96, 3), dtype=np.uint8)
        request = build_perception_request(_snapshot(_array_reading(rgb)))
        mapper = PluginPerceptionMapper(
            plugins=["frame", "floor_plane"],
            plugin_specs=PERCEPTION_PLUGIN_SPECS,
        )

        perception = mapper.perceive(request)

        self.assertEqual(perception.status, "ok")
        self.assertEqual(request.component_summary()["available"], {FRONT_CAMERA_COMPONENT: "CameraFrame"})
        self.assertEqual(perception.artifacts, {})
        frame = next(thing for thing in perception.things if thing.kind == "sensor_frame")
        self.assertEqual(frame.properties["width_px"], 96)

    def test_floor_plugin_emits_boundaries_without_claiming_objects(self) -> None:
        rgb = np.zeros((120, 160, 3), dtype=np.uint8)
        rgb[:70] = (40, 70, 110)
        rgb[70:] = (145, 118, 92)

        result = _mapper("floor_plane").perceive(
            build_perception_request(_snapshot(_array_reading(rgb)))
        )

        boundaries = [thing for thing in result.things if thing.kind == "floor_boundary"]
        self.assertTrue(boundaries)
        self.assertFalse(any(thing.kind == "obstruction_evidence" for thing in result.things))
        bbox = boundaries[0].location.bbox_xyxy_norm
        self.assertIsNotNone(bbox)
        self.assertGreater(bbox[1], 0.45)
        self.assertLess(bbox[3], 0.75)

    def test_windowed_plugin_warms_up_and_reset_discards_previous_frame(self) -> None:
        rgb = np.random.default_rng(7).integers(0, 256, (72, 96, 3), dtype=np.uint8)
        shifted = np.roll(rgb, 2, axis=1)
        mapper = PluginPerceptionMapper(
            plugins=["motion_tracks"],
            plugin_specs=PERCEPTION_PLUGIN_SPECS,
            plugin_configs={
                "motion_tracks": {
                    "max_features": 50,
                    "search_radius": 8,
                    "min_group_size": 4,
                }
            },
        )

        first = mapper.perceive(build_perception_request(_snapshot(_array_reading(rgb), "first")))
        second = mapper.perceive(build_perception_request(_snapshot(_array_reading(shifted), "second")))
        mapper.reset()
        after_reset = mapper.perceive(build_perception_request(_snapshot(_array_reading(rgb), "third")))

        self.assertEqual(first.status, "warming_up")
        self.assertIn(second.status, {"ok", "empty"})
        self.assertEqual(after_reset.status, "warming_up")
        self.assertEqual(second.artifacts, {})

    def test_windowed_tracks_keep_ids_and_expire_after_bounded_misses(self) -> None:
        plugin = MotionTracksPlugin(max_track_misses=1)
        first_candidate = {
            "source_bbox": (0.1, 0.1, 0.3, 0.3),
            "target_bbox": (0.12, 0.1, 0.32, 0.3),
            "confidence": 0.8,
            "kind_hint": "mostly_horizontal_motion",
            "properties": {},
        }
        second_candidate = {
            **first_candidate,
            "source_bbox": first_candidate["target_bbox"],
            "target_bbox": (0.14, 0.1, 0.34, 0.3),
        }

        self.assertEqual(plugin._update_tracks([first_candidate]), [])
        self.assertEqual(plugin._update_tracks([second_candidate]), [])
        self.assertEqual(plugin._tracks[1].support_frames, 2)
        self.assertEqual(plugin._update_tracks([]), [])
        self.assertEqual(plugin._update_tracks([]), [1])
        self.assertEqual(plugin._tracks, {})

    def test_framework_namespaces_declared_diagnostics(self) -> None:
        rgb = np.random.default_rng(11).integers(0, 256, (72, 96, 3), dtype=np.uint8)
        shifted = np.roll(rgb, 2, axis=1)
        mapper = PluginPerceptionMapper(
            plugins=["motion_tracks"],
            plugin_specs=PERCEPTION_PLUGIN_SPECS,
            plugin_configs={"motion_tracks": {"max_features": 50, "search_radius": 8}},
        )
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            mapper.perceive(
                build_perception_request(_snapshot(_array_reading(rgb), "first"), output_dir=output_dir)
            )
            result = mapper.perceive(
                build_perception_request(
                    _snapshot(_array_reading(shifted), "second"),
                    output_dir=output_dir,
                )
            )
            key = "motion-tracks-v0/scene_tracks"
            self.assertIn(key, result.artifacts)
            self.assertTrue(Path(result.artifacts[key]).is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
