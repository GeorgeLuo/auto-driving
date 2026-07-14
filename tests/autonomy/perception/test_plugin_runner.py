from __future__ import annotations

import unittest

import numpy as np

from autonomy.perception import (
    PERCEPTION_TEXT_SCHEMA,
    PerceivedThing,
    PerceptionComponentUnavailable,
    PerceptionEvidenceBatch,
    PerceptionPluginContract,
    PerceptionPluginInput,
    PerceptionSignal,
    ViewLocation,
    build_perception_request,
)
from autonomy.perception.mappers import PluginPerceptionMapper
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorReading, SensorSnapshot
from implementations.perception.catalog import PERCEPTION_PLUGIN_SPECS


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


class PluginRunnerTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
