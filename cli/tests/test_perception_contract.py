from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from autonomy.perception import (
    PerceivedThing,
    PerceptionPluginContract,
    PerceptionPluginResult,
    ViewLocation,
    build_perception_request,
)
from autonomy.perception.mappers.plugin_chain import PluginChainPerceptionMapper
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorReading, SensorSnapshot
from implementations.perception.components import camera_component_id, camera_frame, camera_frame_error
from implementations.perception.motion.tracks import MotionTracksPlugin
from implementations.perception.observation.plugin import FrameObservationPlugin
from implementations.perception.traversability.plugin import FloorPlanePlugin


FRONT_CAMERA_COMPONENT = camera_component_id(FRONT_CAMERA_SENSOR_ID)


class WorkingPlugin:
    plugin_id = "working-test-v0"
    contract = PerceptionPluginContract(
        required_components=("test.component",),
        state_mode="stateless",
    )

    def __init__(self) -> None:
        self.reset_count = 0

    def reset(self) -> None:
        self.reset_count += 1

    def describe_schema(self):
        return {"plugin_id": self.plugin_id}

    def perceive(self, request):
        del request
        thing = PerceivedThing(
            thing_id="test-region",
            kind="region_proposal",
            label="test region",
            location=ViewLocation(frame="image", zone="center"),
            confidence=0.8,
        )
        return PerceptionPluginResult(things=(thing,))


class ExplodingPlugin:
    plugin_id = "exploding-test-v0"
    contract = PerceptionPluginContract(
        required_components=("test.component",),
        state_mode="stateless",
    )

    def reset(self) -> None:
        return None

    def describe_schema(self):
        return {"plugin_id": self.plugin_id}

    def perceive(self, request):
        del request
        raise RuntimeError("expected test failure")


class UnavailablePlugin:
    plugin_id = "unavailable-test-v0"
    contract = PerceptionPluginContract(
        required_components=("test.component",),
        state_mode="stateless",
    )

    def reset(self) -> None:
        return None

    def describe_schema(self):
        return {"plugin_id": self.plugin_id}

    def perceive(self, request):
        del request
        return PerceptionPluginResult(status="unavailable")


class PerceptionContractTests(unittest.TestCase):
    def test_camera_input_normalizes_path_and_array_to_read_only_rgb(self) -> None:
        rgb = np.zeros((12, 16, 3), dtype=np.uint8)
        rgb[:, :, 0] = 220
        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "frame.png"
            Image.fromarray(rgb, mode="RGB").save(image_path)

            path_request = build_perception_request(
                _snapshot(SensorReading(
                    sensor_id=FRONT_CAMERA_SENSOR_ID,
                    sensor_kind="camera",
                    captured_at_ms=10,
                    path=str(image_path),
                ))
            )
            array_request = build_perception_request(
                _snapshot(SensorReading(
                    sensor_id=FRONT_CAMERA_SENSOR_ID,
                    sensor_kind="camera",
                    captured_at_ms=11,
                    value=rgb.copy(),
                    metadata={"color_space": "RGB"},
                ))
            )
            self.assertEqual(path_request.component_summary()["available"], {})
            self.assertEqual(array_request.component_summary()["available"], {})
            path_frame = camera_frame(path_request, FRONT_CAMERA_SENSOR_ID)
            array_frame = camera_frame(array_request, FRONT_CAMERA_SENSOR_ID)
            self.assertIsNotNone(path_frame)
            self.assertIsNotNone(array_frame)
            np.testing.assert_array_equal(path_frame.rgb, array_frame.rgb)
            self.assertIs(camera_frame(path_request, FRONT_CAMERA_SENSOR_ID), path_frame)
            self.assertFalse(path_frame.rgb.flags.writeable)
            self.assertEqual(path_frame.to_dict()["color_space"], "RGB")
            self.assertEqual(array_frame.metadata["normalized_from"], "value")

    def test_invalid_camera_input_is_unavailable_not_negative_evidence(self) -> None:
        request = build_perception_request(
            _snapshot(SensorReading(
                sensor_id=FRONT_CAMERA_SENSOR_ID,
                sensor_kind="camera",
                captured_at_ms=10,
                value=object(),
            ))
        )

        result = FrameObservationPlugin().perceive(request)

        self.assertEqual(result.status, "unavailable")
        self.assertIn(
            "unsupported camera value type",
            camera_frame_error(request, FRONT_CAMERA_SENSOR_ID) or "",
        )
        self.assertEqual(result.things, ())

    def test_mapper_schema_aggregates_plugin_component_queries(self) -> None:
        mapper = PluginChainPerceptionMapper(
            plugins=["frame"],
            plugin_specs={
                "frame": "implementations.perception.observation.plugin:FrameObservationPlugin"
            },
        )

        schema = mapper.describe_schema()

        self.assertEqual(schema["inputs"][0]["component_id"], FRONT_CAMERA_COMPONENT)
        self.assertEqual(schema["inputs"][0]["required_by"], ["frame-observation-v0"])

    def test_mapper_isolates_plugin_errors_and_reports_timing(self) -> None:
        request = build_perception_request(
            _snapshot(SensorReading(
                sensor_id=FRONT_CAMERA_SENSOR_ID,
                sensor_kind="camera",
                captured_at_ms=10,
                value=np.zeros((8, 8, 3), dtype=np.uint8),
            ))
        )
        mapper = PluginChainPerceptionMapper(
            plugins=["working", "exploding"],
            plugin_specs={
                "working": f"{__name__}:WorkingPlugin",
                "exploding": f"{__name__}:ExplodingPlugin",
            },
        )

        perception = mapper.perceive(request)

        self.assertEqual(perception.schema, "perception_text_v1")
        self.assertEqual(perception.status, "partial")
        self.assertEqual([run.status for run in perception.plugin_runs], ["ok", "error"])
        self.assertTrue(all(run.duration_ms >= 0 for run in perception.plugin_runs))
        self.assertIn("RuntimeError: expected test failure", perception.plugin_runs[1].error or "")
        self.assertEqual([thing.thing_id for thing in perception.things], ["test-region"])

    def test_mapper_reset_invokes_each_plugin(self) -> None:
        mapper = PluginChainPerceptionMapper(
            plugins=["working"],
            plugin_specs={"working": f"{__name__}:WorkingPlugin"},
        )
        plugin = mapper.plugins[0]

        mapper.reset()
        mapper.reset()

        self.assertEqual(plugin.reset_count, 2)

    def test_mapper_reports_partial_when_one_plugin_is_unavailable(self) -> None:
        mapper = PluginChainPerceptionMapper(
            plugins=["working", "unavailable"],
            plugin_specs={
                "working": f"{__name__}:WorkingPlugin",
                "unavailable": f"{__name__}:UnavailablePlugin",
            },
        )

        perception = mapper.perceive(
            build_perception_request(_snapshot(_array_reading(np.zeros((8, 8, 3), dtype=np.uint8), 10)))
        )

        self.assertEqual(perception.status, "partial")
        self.assertEqual([run.status for run in perception.plugin_runs], ["ok", "unavailable"])

    def test_current_plugins_accept_in_memory_camera_without_artifact_writes(self) -> None:
        rgb = np.random.default_rng(3).integers(0, 256, (72, 96, 3), dtype=np.uint8)
        request = build_perception_request(
            _snapshot(SensorReading(
                sensor_id=FRONT_CAMERA_SENSOR_ID,
                sensor_kind="camera",
                captured_at_ms=10,
                value=rgb,
            ))
        )

        frame = FrameObservationPlugin().perceive(request)
        floor = FloorPlanePlugin(write_artifacts=False).perceive(request)

        self.assertEqual(frame.status, "ok")
        self.assertEqual(frame.things[0].properties["width_px"], 96)
        self.assertEqual(floor.status, "ok")
        self.assertEqual(floor.artifacts, {})

    def test_floor_plugin_emits_first_hit_boundaries_without_claiming_objects(self) -> None:
        rgb = np.zeros((120, 160, 3), dtype=np.uint8)
        rgb[:70] = (40, 70, 110)
        rgb[70:] = (145, 118, 92)
        plugin = FloorPlanePlugin(write_artifacts=False)

        result = plugin.perceive(
            build_perception_request(_snapshot(_array_reading(rgb, 10)))
        )

        boundaries = [thing for thing in result.things if thing.kind == "floor_boundary"]
        self.assertTrue(boundaries)
        self.assertFalse(any(thing.kind == "obstruction_evidence" for thing in result.things))
        bbox = boundaries[0].location.bbox_xyxy_norm
        self.assertIsNotNone(bbox)
        self.assertGreater(bbox[1], 0.45)
        self.assertLess(bbox[3], 0.75)
        self.assertLessEqual(result.things[0].properties["boundary_hit_fraction_columns"], 1.0)

    def test_windowed_plugin_warms_up_and_reset_discards_previous_frame(self) -> None:
        rgb = np.random.default_rng(7).integers(0, 256, (72, 96, 3), dtype=np.uint8)
        shifted = np.roll(rgb, 2, axis=1)
        plugin = MotionTracksPlugin(
            write_artifacts=False,
            max_features=50,
            search_radius=8,
            min_group_size=4,
        )

        first = plugin.perceive(build_perception_request(_snapshot(_array_reading(rgb, 10))))
        second = plugin.perceive(build_perception_request(_snapshot(_array_reading(shifted, 11))))
        plugin.reset()
        after_reset = plugin.perceive(build_perception_request(_snapshot(_array_reading(rgb, 12))))

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
        self.assertEqual(list(plugin._tracks), [1])
        self.assertEqual(plugin._update_tracks([second_candidate]), [])
        self.assertEqual(list(plugin._tracks), [1])
        self.assertEqual(plugin._tracks[1].support_frames, 2)
        self.assertEqual(plugin._update_tracks([]), [])
        self.assertEqual(plugin._tracks[1].missed_frames, 1)
        self.assertEqual(plugin._update_tracks([]), [1])
        self.assertEqual(plugin._tracks, {})

    def test_windowed_track_artifact_labels_current_tracks_when_recording(self) -> None:
        rgb = np.random.default_rng(11).integers(0, 256, (72, 96, 3), dtype=np.uint8)
        shifted = np.roll(rgb, 2, axis=1)
        plugin = MotionTracksPlugin(
            max_features=50,
            search_radius=8,
            min_group_size=4,
        )
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            plugin.perceive(
                build_perception_request(
                    _snapshot(_array_reading(rgb, 10)), output_dir=output_dir
                )
            )
            result = plugin.perceive(
                build_perception_request(
                    _snapshot(_array_reading(shifted, 11)), output_dir=output_dir
                )
            )

            self.assertIn("scene_tracks", result.artifacts)
            self.assertTrue(Path(result.artifacts["scene_tracks"]).is_file())


def _snapshot(reading: SensorReading) -> SensorSnapshot:
    return SensorSnapshot(
        read_id="test-frame",
        readings={reading.sensor_id: reading},
        started_at_ms=reading.captured_at_ms,
        completed_at_ms=reading.captured_at_ms,
    )


def _array_reading(rgb: np.ndarray, captured_at_ms: int) -> SensorReading:
    return SensorReading(
        sensor_id=FRONT_CAMERA_SENSOR_ID,
        sensor_kind="camera",
        captured_at_ms=captured_at_ms,
        value=rgb,
    )


if __name__ == "__main__":
    unittest.main(verbosity=2)
