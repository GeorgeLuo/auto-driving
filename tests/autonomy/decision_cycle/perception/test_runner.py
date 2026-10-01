from __future__ import annotations

from autonomy.plugins import PluginManager

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
from autonomy.decision_cycle.perception.components.interface import (
    PerceptionComponentUnavailable,
)
from autonomy.decision_cycle.perception.plugin import (
    PerceptionPluginContract,
    PerceptionPluginInput,
)
from autonomy.decision_cycle.perception.inputs import build_perception_request
from autonomy.decision_cycle.perception.runner import PerceptionRunner
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorReading, SensorSnapshot
from implementations.decision_cycle.perception.catalog import PERCEPTION_PLUGIN_SPECS


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
        mapper = PerceptionRunner.from_selection(
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
        self.assertEqual(perception.signals[0].source_plugin_id, "working")
        self.assertEqual(perception.things[0].source_plugin_id, "working")
        self.assertEqual(perception.plugin_runs[0].plugin_id, "working")
        self.assertEqual(perception.plugin_runs[0].plugin_id, "working")
        self.assertEqual(mapper.plugins[0].plugin_id, "working")
        self.assertEqual(mapper.plugins[0].asserted_value, 42)
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

    def test_runner_reset_is_optional_and_invokes_stateful_hook_when_present(self) -> None:
        mapper = PerceptionRunner.from_selection(
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
        mapper = PerceptionRunner.from_selection(
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

    def test_schema_uses_full_manager_catalog_without_constructing_unselected_plugins(self) -> None:
        manager = PluginManager.from_specs("perception", {
            "working": f"{__name__}:WorkingPlugin",
            "broken": f"{__name__}:ConstructionFailurePlugin",
        })
        mapper = PerceptionRunner(plugin_manager=manager)
        self.assertEqual(
            mapper.describe_schema()["configuration"]["available_plugins"], ["broken", "working"],
        )
        self.assertEqual(mapper.plugins, ())
        manager.add("working")
        mapper.perceive(build_perception_request(_snapshot(_array_reading())))
        self.assertEqual(
            mapper.describe_schema()["configuration"]["available_plugins"], ["broken", "working"],
        )
        self.assertEqual(set(mapper.plugin_specs), {"broken", "working"})
        manager.remove("working")
        mapper.perceive(build_perception_request(_snapshot(_array_reading())))
        self.assertEqual(
            mapper.describe_schema()["configuration"]["available_plugins"], ["broken", "working"],
        )
        self.assertEqual(mapper.plugins, ())

    def test_manager_selection_is_applied_at_the_next_perception_frame(self) -> None:
        manager = PluginManager.from_specs(
            "perception",
            {
                "working": f"{__name__}:WorkingPlugin",
                "unavailable": f"{__name__}:UnavailablePlugin",
            }
        )
        manager.select(["working"])
        mapper = PerceptionRunner(plugin_manager=manager)
        working = mapper.plugins[0]

        first = mapper.perceive(
            build_perception_request(_snapshot(_array_reading(), "frame-1"))
        )
        self.assertEqual([run.plugin_id for run in first.plugin_runs], ["working"])

        manager.add("unavailable")
        self.assertEqual(mapper.plugin_ids, ("working",))
        second = mapper.perceive(
            build_perception_request(_snapshot(_array_reading(), "frame-2"))
        )
        self.assertEqual(
            [run.plugin_id for run in second.plugin_runs],
            ["working", "unavailable"],
        )
        self.assertIs(mapper.plugins[0], working)

        manager.remove("working")
        third = mapper.perceive(
            build_perception_request(_snapshot(_array_reading(), "frame-3"))
        )
        self.assertEqual(
            [run.plugin_id for run in third.plugin_runs], ["unavailable"]
        )
        self.assertNotIn("test_ready", [signal.name for signal in third.signals])
        self.assertEqual(working.reset_count, 1)


    def test_selection_change_during_perceive_applies_on_the_next_frame(self) -> None:
        manager = PluginManager.from_specs(
            "perception",
            {
                "selection_changing": f"{__name__}:SelectionChangingPlugin",
                "working": f"{__name__}:WorkingPlugin",
            }
        )
        manager.select(["selection_changing", "working"])
        SelectionChangingPlugin.manager = manager
        try:
            mapper = PerceptionRunner(plugin_manager=manager)
            first = mapper.perceive(
                build_perception_request(_snapshot(_array_reading(), "frame-1"))
            )
            second = mapper.perceive(
                build_perception_request(_snapshot(_array_reading(), "frame-2"))
            )

            self.assertEqual(
                [run.plugin_id for run in first.plugin_runs],
                ["selection_changing", "working"],
            )
            self.assertEqual(
                [run.plugin_id for run in second.plugin_runs], ["working"]
            )
        finally:
            SelectionChangingPlugin.manager = None

    def test_prepare_selection_publishes_the_same_instances_on_commit(self) -> None:
        manager = PluginManager.from_specs(
            "perception",
            {
                "working": f"{__name__}:WorkingPlugin",
                "unavailable": f"{__name__}:UnavailablePlugin",
            }
        )
        manager.select(["working"])
        mapper = PerceptionRunner(plugin_manager=manager)
        working = mapper.plugins[0]
        manager.select(["unavailable"])

        mapper.prepare_selection()

        self.assertEqual(mapper.plugin_ids, ("working",))
        self.assertIs(mapper.plugins[0], working)
        self.assertEqual(working.reset_count, 0)
        mapper.commit_selection()
        self.assertEqual(mapper.plugin_ids, ("unavailable",))
        self.assertIsNot(mapper.plugins[0], working)
        self.assertEqual(working.reset_count, 1)


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
                        "metadata": {"algorithm": "test-observer"},
                    }
                ),
                encoding="utf-8",
            )
            step = PerceptionRunner.from_activation(read_step_activation(path, "perception"))

        pending = step.status()
        self.assertEqual(pending["plugin_report"]["selected_plugin_ids"], ["working"])
        self.assertEqual(pending["plugin_report"]["applied_plugin_ids"], ["working"])
        self.assertIsNone(pending["plugin_report"]["plugins"][0]["duration_ms"])
        self.assertEqual(pending["last_plugin_runs"], [])

        result = step(
            DecisionFrameContext(
                frame_id="frame-1",
                frame_index=3,
                timestamp_ms=10,
                sensor_snapshot=_snapshot(_array_reading()),
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
        self.assertEqual(step.plugins[0].plugin_id, "working")

        step.plugin_manager.select(["exploding"])
        waiting = step.status()
        self.assertEqual(waiting["plugin_report"]["selected_plugin_ids"], ["exploding"])
        self.assertEqual(waiting["plugin_report"]["applied_plugin_ids"], ["working"])
        self.assertEqual(waiting["plugin_report"]["plugins"][0]["plugin_id"], "working")
        self.assertEqual(
            waiting["plugin_report"]["plugins"][0]["duration_ms"],
            record["duration_ms"],
        )
        self.assertEqual(waiting["last_plugin_runs"][0]["plugin_id"], "working")

        failed = step(
            DecisionFrameContext(
                frame_id="frame-2",
                frame_index=4,
                timestamp_ms=20,
                sensor_snapshot=_snapshot(_array_reading()),
            )
        )
        reported = step.status()
        failed_record = reported["plugin_report"]["plugins"][0]
        self.assertEqual(reported["plugin_report"]["applied_plugin_ids"], ["exploding"])
        self.assertEqual(reported["plugin_report"]["selected_plugin_ids"], ["exploding"])
        self.assertEqual(failed_record["plugin_id"], "exploding")
        self.assertEqual(failed_record["plugin_id"], "exploding")
        self.assertGreaterEqual(failed_record["duration_ms"], 0)
        self.assertIn("expected test failure", failed_record["error"])
        self.assertNotIn("status", failed_record)
        self.assertEqual(reported["last_plugin_runs"][0]["status"], "error")
        self.assertEqual(reported["last_plugin_runs"][0]["plugin_id"], "exploding")
        self.assertEqual(failed.plugin_runs[0].plugin_id, "exploding")
        self.assertEqual(step.plugins[0].plugin_id, "exploding")
        self.assertEqual(step.plugin_report(), reported["plugin_report"])

        step.reset()
        cleared = step.status()
        self.assertEqual(cleared["last_plugin_runs"], [])
        self.assertIsNone(cleared["plugin_report"]["plugins"][0]["duration_ms"])
        self.assertIsNone(cleared["plugin_report"]["plugins"][0]["error"])
        self.assertEqual(step.plugin_report(), cleared["plugin_report"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
