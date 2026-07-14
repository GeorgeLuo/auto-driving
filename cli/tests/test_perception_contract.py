from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from autonomy.perception import (
    PERCEPTION_TEXT_SCHEMA,
    PerceivedThing,
    PerceptionComponentUnavailable,
    PerceptionEvidenceBatch,
    PerceptionPluginContract,
    PerceptionPluginInput,
    PerceptionSignal,
    PerceptionText,
    ViewLocation,
    build_perception_request,
)
from autonomy.perception.mappers import PluginPerceptionMapper
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorReading, SensorSnapshot
from implementations.perception.catalog import PERCEPTION_PLUGIN_SPECS
from implementations.perception.components import (
    FRONT_CAMERA_RGB_INPUT,
    camera_component_id,
    provide_camera_frame,
)
from implementations.perception.motion.tracks import MotionTracksPlugin


FRONT_CAMERA_COMPONENT = camera_component_id(FRONT_CAMERA_SENSOR_ID)
TEST_INPUT = PerceptionPluginInput(
    name="value",
    component_id="test.component",
    provider_spec=f"{__name__}:provide_test_component",
)
UNAVAILABLE_INPUT = PerceptionPluginInput(
    name="missing",
    component_id="test.unavailable",
    provider_spec=f"{__name__}:provide_unavailable_component",
)


def provide_test_component(request, plugin_input):
    del request, plugin_input
    return {"value": 42}


def provide_unavailable_component(request, plugin_input):
    del request, plugin_input
    raise PerceptionComponentUnavailable("test component is absent")


class WorkingPlugin:
    plugin_id = "working-test-v0"
    contract = PerceptionPluginContract(
        inputs=(TEST_INPUT,),
        description="Test fixture that emits one signal and one thing.",
        emits=("signal test_ready", "thing test-region"),
    )

    def __init__(self) -> None:
        self.reset_count = 0

    def reset(self) -> None:
        self.reset_count += 1

    def perceive(self, inputs):
        self.asserted_value = inputs.require("value", dict)["value"]
        return PerceptionEvidenceBatch(
            signals=(PerceptionSignal("test_ready", True),),
            things=(
                PerceivedThing(
                    thing_id="test-region",
                    kind="region_proposal",
                    label="test region",
                    location=ViewLocation(frame="image", zone="center"),
                    confidence=0.8,
                ),
            ),
        )


class ExplodingPlugin:
    plugin_id = "exploding-test-v0"
    contract = PerceptionPluginContract(inputs=(TEST_INPUT,))

    def perceive(self, inputs):
        del inputs
        raise RuntimeError("expected test failure")


class UnavailablePlugin:
    plugin_id = "unavailable-test-v0"
    contract = PerceptionPluginContract(inputs=(UNAVAILABLE_INPUT,))

    def __init__(self) -> None:
        self.invocations = 0

    def perceive(self, inputs):
        del inputs
        self.invocations += 1
        return PerceptionEvidenceBatch()


class PerceptionContractTests(unittest.TestCase):
    def test_polygon_and_signal_survive_perception_serialization(self) -> None:
        polygon = ((0.1, 0.2), (0.7, 0.25), (0.6, 0.8), (0.2, 0.75))
        original = PerceptionText(
            schema=PERCEPTION_TEXT_SCHEMA,
            plugin_id="test-plugin",
            status="ok",
            lines=("signal id=ready value=true", "thing id=region"),
            signals=(PerceptionSignal("ready", True, source_plugin_id="fixture"),),
            things=(
                PerceivedThing(
                    thing_id="region",
                    kind="region_proposal",
                    label="region",
                    location=ViewLocation(
                        frame="image",
                        zone="center",
                        bbox_xyxy_norm=(0.1, 0.2, 0.7, 0.8),
                        polygon_xy_norm=polygon,
                    ),
                    confidence=0.8,
                    source_plugin_id="fixture",
                ),
            ),
        )

        restored = PerceptionText.from_dict(original.to_dict())

        self.assertEqual(restored.signals[0].signal_id, "ready")
        self.assertEqual(restored.signals[0].source_plugin_id, "fixture")
        self.assertEqual(restored.things[0].location.polygon_xy_norm, polygon)
        self.assertEqual(restored.things[0].location.bbox_xyxy_norm, (0.1, 0.2, 0.7, 0.8))

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

    def test_runner_injects_inputs_attributes_evidence_and_isolates_errors(self) -> None:
        mapper = PluginPerceptionMapper(
            plugins=["working", "exploding"],
            plugin_specs={
                "working": f"{__name__}:WorkingPlugin",
                "exploding": f"{__name__}:ExplodingPlugin",
            },
        )

        perception = mapper.perceive(build_perception_request(_snapshot(_array_reading())))

        self.assertEqual(perception.schema, PERCEPTION_TEXT_SCHEMA)
        self.assertEqual(perception.status, "partial")
        self.assertEqual([run.status for run in perception.plugin_runs], ["ok", "error"])
        self.assertTrue(all(run.duration_ms >= 0 for run in perception.plugin_runs))
        self.assertIn("RuntimeError: expected test failure", perception.plugin_runs[1].error or "")
        self.assertEqual(perception.signals[0].source_plugin_id, "working-test-v0")
        self.assertEqual(perception.things[0].source_plugin_id, "working-test-v0")
        self.assertEqual(mapper.plugins[0].asserted_value, 42)

    def test_runner_reset_is_optional_and_invokes_stateful_hook_when_present(self) -> None:
        mapper = PluginPerceptionMapper(
            plugins=["working", "frame"],
            plugin_specs={
                "working": f"{__name__}:WorkingPlugin",
                "frame": PERCEPTION_PLUGIN_SPECS["frame"],
            },
        )
        plugin = mapper.plugins[0]

        mapper.reset()
        mapper.reset()

        self.assertEqual(plugin.reset_count, 2)

    def test_missing_input_short_circuits_plugin_as_unavailable(self) -> None:
        mapper = PluginPerceptionMapper(
            plugins=["working", "unavailable"],
            plugin_specs={
                "working": f"{__name__}:WorkingPlugin",
                "unavailable": f"{__name__}:UnavailablePlugin",
            },
        )

        perception = mapper.perceive(build_perception_request(_snapshot(_array_reading())))

        self.assertEqual(perception.status, "partial")
        self.assertEqual([run.status for run in perception.plugin_runs], ["ok", "unavailable"])
        self.assertEqual(mapper.plugins[1].invocations, 0)

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
