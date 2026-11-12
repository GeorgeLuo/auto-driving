from __future__ import annotations



import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.perception.interface import PERCEPTION_TEXT_SCHEMA
from autonomy.decision_cycle.activation import STEP_ACTIVATION_SCHEMA, read_step_activation
from autonomy.decision_cycle.perception.evidence.values import (
    PerceivedThing,
    PerceptionEvidenceBatch,
    PerceptionSignal,
    ViewLocation,
)
from autonomy.decision_cycle.perception.feeds.interface import (
    PerceptionFeedUnavailable,
)
from autonomy.decision_cycle.perception.plugin import (
    PerceptionPluginContract,
    PerceptionPluginInput,
)
from autonomy.decision_cycle.perception.inputs import build_perception_request
from autonomy.decision_cycle.perception.runner import PerceptionRunner
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorFrame, SensorReading
from implementations.decision_cycle.catalog import step_plugins


TEST_INPUT = PerceptionPluginInput(
    name="value",
    feed_id="test.feed",
    provider_spec=f"{__name__}:provide_test_feed",
)
UNAVAILABLE_INPUT = PerceptionPluginInput(
    name="missing",
    feed_id="test.unavailable",
    provider_spec=f"{__name__}:provide_unavailable_feed",
)


def provide_test_feed(request, plugin_input):
    del request, plugin_input
    return {"value": 42}


def provide_unavailable_feed(request, plugin_input):
    del request, plugin_input
    raise PerceptionFeedUnavailable("test feed is absent")


PERCEIVED_VALUES: list[int] = []  # WorkingPlugin's inputs, read by the tests; the plugin keeps nothing


class WorkingPlugin:
    plugin_id = "working"
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
        PERCEIVED_VALUES.append(inputs.require("value", dict)["value"])
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


class MemoryRequiredNoArgReset:
    plugin_id = "memory_required_reset"
    contract = PerceptionPluginContract(
        memory_required=True,
        state_mode="windowed",
    )

    def __init__(self) -> None:
        self.reset_count = 0

    def reset(self) -> None:
        self.reset_count += 1

    def perceive(self, inputs):
        del inputs
        return PerceptionEvidenceBatch()


class ExplodingPlugin:
    plugin_id = "exploding"
    contract = PerceptionPluginContract(inputs=(TEST_INPUT,))

    def perceive(self, inputs):
        del inputs
        raise RuntimeError("expected test failure")


class UnavailablePlugin:
    plugin_id = "unavailable"
    contract = PerceptionPluginContract(inputs=(UNAVAILABLE_INPUT,))

    def __init__(self) -> None:
        self.invocations = 0

    def perceive(self, inputs):
        del inputs
        self.invocations += 1
        return PerceptionEvidenceBatch()


class ConstructionFailurePlugin:
    plugin_id = "broken"
    contract = PerceptionPluginContract()

    def __init__(self) -> None:
        raise RuntimeError("expected construction failure")


class SelectionChangingPlugin:
    plugin_id = "selection_changing"
    contract = PerceptionPluginContract()
    manager = None

    def perceive(self, inputs):
        del inputs
        self.manager.remove("selection_changing")
        return PerceptionEvidenceBatch(
            signals=(PerceptionSignal("selection_changed", True),)
        )


def _sensor_frame(reading: SensorReading, read_id: str = "test-frame") -> SensorFrame:
    return SensorFrame(
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
        mapper = PerceptionRunner.from_selection(
            plugins=["working", "exploding"],
            plugin_specs={
                "working": f"{__name__}:WorkingPlugin",
                "exploding": f"{__name__}:ExplodingPlugin",
            },
        )

        perception = mapper.perceive(build_perception_request(_sensor_frame(_array_reading())))

        self.assertEqual(perception.schema, PERCEPTION_TEXT_SCHEMA)
        self.assertEqual(perception.status, "partial")
        self.assertEqual([run.status for run in perception.plugin_runs], ["ok", "error"])
        self.assertTrue(all(run.duration_ms >= 0 for run in perception.plugin_runs))
        self.assertIn("RuntimeError: expected test failure", perception.plugin_runs[1].error or "")
        self.assertEqual(perception.signals[0].source_plugin_id, "working")
        self.assertEqual(perception.things[0].source_plugin_id, "working")
        self.assertEqual(perception.plugin_runs[0].plugin_id, "working")
        self.assertEqual(perception.plugin_runs[0].plugin_id, "working")
        self.assertEqual(list(mapper.plugins), ["working", "exploding"])
        self.assertEqual(PERCEIVED_VALUES[-1], 42)
        reported = {
            item["plugin_id"]: item for item in mapper.plugin_report()["plugins"]
        }
        for run in perception.plugin_runs:
            record = reported[run.plugin_id]
            self.assertEqual(
                set(record),
                {"plugin_id", "duration_ms", "error"},
            )
            self.assertEqual(record["plugin_id"], run.plugin_id)
            self.assertEqual(record["duration_ms"], run.duration_ms)
            self.assertEqual(record["error"], run.error)

    def test_a_runner_built_from_a_selection_names_no_activation_file(self) -> None:
        mapper = PerceptionRunner.from_selection(
            plugins=["working"], plugin_specs={"working": f"{__name__}:WorkingPlugin"},
        )
        requests = []
        perceive = mapper.perceive
        mapper.perceive = lambda request: requests.append(request) or perceive(request)

        mapper(DecisionFrameContext("frame-1", 3, 1_000, sensor_frame=_sensor_frame(_array_reading())))

        self.assertEqual(requests[0].metadata, {"activation": None, "frame_index": 3})

    def test_cycle_runner_forwards_context_metadata_and_owns_frame_identity(self) -> None:
        mapper = PerceptionRunner.from_selection(
            plugins=["working"], plugin_specs={"working": f"{__name__}:WorkingPlugin"},
        )
        requests = []
        perceive = mapper.perceive
        mapper.perceive = lambda request: requests.append(request) or perceive(request)
        mapper(DecisionFrameContext(
            "frame-1", 3, 1000, sensor_frame=_sensor_frame(_array_reading()),
            metadata={"source": "replay", "sequence_index": 2, "frame_index": 99, "activation": "caller"},
        ))
        self.assertEqual(requests[0].metadata, {
            "source": "replay", "sequence_index": 2, "frame_index": 3, "activation": None,
        })

    def test_runner_reset_is_optional_and_invokes_stateful_hook_when_present(self) -> None:
        mapper = PerceptionRunner.from_selection(
            plugins=["working", "frame"],
            plugin_specs={
                "working": f"{__name__}:WorkingPlugin",
                "frame": step_plugins("perception")["frame"]["spec"],
            },
        )
        plugin = mapper.plugins["working"]

        mapper.reset()
        mapper.reset()

        self.assertEqual(plugin.reset_count, 2)

    def test_memory_required_plugin_with_no_argument_reset_is_reset(self) -> None:
        mapper = PerceptionRunner.from_selection(
            plugins=["memory_required_reset"],
            plugin_specs={
                "memory_required_reset": f"{__name__}:MemoryRequiredNoArgReset",
            },
        )

        mapper.reset({})

        self.assertEqual(mapper.plugins["memory_required_reset"].reset_count, 1)

    def test_missing_input_short_circuits_plugin_as_unavailable(self) -> None:
        mapper = PerceptionRunner.from_selection(
            plugins=["working", "unavailable"],
            plugin_specs={
                "working": f"{__name__}:WorkingPlugin",
                "unavailable": f"{__name__}:UnavailablePlugin",
            },
        )

        perception = mapper.perceive(build_perception_request(_sensor_frame(_array_reading())))

        self.assertEqual(perception.status, "partial")
        self.assertEqual([run.status for run in perception.plugin_runs], ["ok", "unavailable"])
        self.assertEqual(mapper.plugins["unavailable"].invocations, 0)

    def test_repeated_calls_keep_the_constructed_instances(self) -> None:
        mapper = PerceptionRunner.from_selection(
            plugins=["working"],
            plugin_specs={"working": f"{__name__}:WorkingPlugin"},
        )
        working = mapper.plugins["working"]
        frame = _sensor_frame(_array_reading(), "frame-1")
        mapper.perceive(build_perception_request(frame))
        mapper.perceive(build_perception_request(_sensor_frame(_array_reading(), "frame-2")))
        self.assertIs(mapper.plugins["working"], working)
        self.assertEqual(mapper.plugin_report()["applied_plugin_ids"], ["working"])

    def test_step_status_reports_applied_runs_with_catalog_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "active.json"
            path.write_text(
                json.dumps(
                    {
                        "schema": STEP_ACTIVATION_SCHEMA,
                        "step": "perception",
                        "plugins": ["working"],
                        "plugin_specs": {
                            "working": f"{__name__}:WorkingPlugin",
                            "exploding": f"{__name__}:ExplodingPlugin",
                        },
                        "metadata": {"preset": "test-observer"},
                    }
                ),
                encoding="utf-8",
            )
            step = PerceptionRunner.from_activation(read_step_activation(path, "perception"))

        pending = step.status()
        self.assertEqual(pending["selected_plugin_ids"], ["working"])
        self.assertEqual(pending["plugin_report"]["applied_plugin_ids"], ["working"])
        self.assertIsNone(pending["plugin_report"]["plugins"][0]["duration_ms"])
        self.assertEqual(pending["last_plugin_runs"], [])

        result = step(
            DecisionFrameContext(
                frame_id="frame-1",
                frame_index=3,
                timestamp_ms=10,
                sensor_frame=_sensor_frame(_array_reading()),
            )
        )
        executed = step.status()
        record = executed["plugin_report"]["plugins"][0]
        self.assertEqual(record["plugin_id"], "working")
        self.assertEqual(record["plugin_id"], "working")
        self.assertGreaterEqual(record["duration_ms"], 0)
        self.assertIsNone(record["error"])
        self.assertEqual(
            set(record),
            {"plugin_id", "duration_ms", "error"},
        )
        self.assertEqual(executed["last_plugin_runs"][0]["status"], "ok")
        self.assertEqual(executed["last_plugin_runs"][0]["duration_ms"], record["duration_ms"])
        self.assertEqual(step.plugin_report(), executed["plugin_report"])
        self.assertEqual(result.plugin_runs[0].plugin_id, "working")
        self.assertEqual(list(step.plugins), ["working"])
        self.assertEqual(step.status()["plugin_report"]["applied_plugin_ids"], ["working"])

        failed_step = PerceptionRunner.from_selection(
            plugins=["exploding"],
            plugin_specs={"exploding": f"{__name__}:ExplodingPlugin"},
        )
        failed = failed_step(
            DecisionFrameContext(
                frame_id="frame-2",
                frame_index=4,
                timestamp_ms=20,
                sensor_frame=_sensor_frame(_array_reading()),
            )
        )
        reported = failed_step.status()
        failed_record = reported["plugin_report"]["plugins"][0]
        self.assertEqual(reported["selected_plugin_ids"], ["exploding"])
        self.assertEqual(reported["plugin_report"]["applied_plugin_ids"], ["exploding"])
        self.assertEqual(failed_record["plugin_id"], "exploding")
        self.assertEqual(failed_record["plugin_id"], "exploding")
        self.assertGreaterEqual(failed_record["duration_ms"], 0)
        self.assertIn("expected test failure", failed_record["error"])
        self.assertNotIn("status", failed_record)
        self.assertEqual(reported["last_plugin_runs"][0]["status"], "error")
        self.assertEqual(reported["last_plugin_runs"][0]["plugin_id"], "exploding")
        self.assertEqual(failed.plugin_runs[0].plugin_id, "exploding")
        self.assertEqual(list(failed_step.plugins), ["exploding"])
        self.assertEqual(failed_step.plugin_report(), reported["plugin_report"])
        self.assertEqual(step.plugin_ids, ("working",))

        failed_step.reset()
        cleared = failed_step.status()
        self.assertEqual(cleared["last_plugin_runs"], [])
        self.assertIsNone(cleared["plugin_report"]["plugins"][0]["duration_ms"])
        self.assertIsNone(cleared["plugin_report"]["plugins"][0]["error"])
        self.assertEqual(failed_step.plugin_report(), cleared["plugin_report"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
