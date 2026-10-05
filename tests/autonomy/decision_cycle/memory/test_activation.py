from __future__ import annotations
import tempfile
import unittest
from autonomy.decision_cycle.cycle import DecisionCycle, DecisionSteps
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.memory.interface import MEMORY_REPORT_SCHEMA
from autonomy.decision_cycle.memory.runner import MemoryRunner
from autonomy.decision_cycle.activation import read_step_activation
from tests.autonomy.decision_cycle.memory.activation_fixtures import (
    _valid_payload,
    _write_payload,
)


def _state(report: dict) -> dict:
    return report["plugins"][-1]["state"]


class MemoryActivationTests(unittest.TestCase):
    def test_activation_loads_and_runs_through_decision_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            activation = read_step_activation(_write_payload(tmp, _valid_payload()), "memory")
            step = MemoryRunner.from_activation(activation)
            shared: dict = {}

            context = DecisionFrameContext(
                frame_id="frame_1",
                frame_index=1,
                timestamp_ms=100,
                shared_memory=shared,
            )
            observation = Observation(
                observation_id="obs_1",
                created_at_ms=90,
                sensor_frame={},
                summary=("hello",),
            )
            result = DecisionCycle(
                DecisionSteps(memory=step),
            ).run(context)
            # no observation step => observation is None on first cycle
            self.assertEqual(result.memory["schema"], MEMORY_REPORT_SCHEMA)
            self.assertEqual(_state(result.memory)["record_count"], 0)

            remembered = _state(step(context, observation))
            self.assertEqual(remembered["record_count"], 1)
            self.assertEqual(
                remembered["records"][0]["origin"]["observation_id"],
                "obs_1",
            )
            self.assertEqual(shared["recording_test.state"]["records"][0].record_id, "rec-obs_1")
            status = step.status()
            self.assertEqual(status["plugin_ids"], ["recording_test"])
            # A step holds a list of plugins; none of them stands for the step.
            self.assertNotIn("plugin_id", status)
            self.assertNotIn("plugin_spec", status)
            self.assertEqual(status["update_count"], 2)
            self.assertEqual(status["failure_count"], 0)
            self.assertIsNotNone(status["last_duration_ms"])
            self.assertEqual(status["plugins"][0]["state"]["record_count"], 1)

    def test_reset_starts_a_new_empty_epoch_and_returns_the_written_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            step = MemoryRunner.from_activation(
                read_step_activation(_write_payload(tmp, _valid_payload()), "memory")
            )
            shared: dict = {"other.key": "kept by the host, not by memory"}
            step.update(
                DecisionFrameContext("frame_2", 2, 200, shared_memory=shared),
                Observation("obs_2", 190, {}),
            )

            written = step.reset(shared)

            self.assertEqual(set(written), {"recording_test.state"})
            state = _state(step.report())
            self.assertEqual(state["record_count"], 0)
            self.assertEqual(state["epoch_id"], "epoch-2")


    def test_reset_failures_are_recorded_without_raising(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = _valid_payload()
            payload["plugin_configs"]["recording_test"]["fail_on_reset"] = True
            step = MemoryRunner.from_activation(
                read_step_activation(_write_payload(tmp, payload), "memory")
            )

            self.assertEqual(step.reset({}), {})

            self.assertEqual(step.failure_count, 1)
            self.assertIn("reset exploded", step.last_error or "")

    def test_report_is_detached_from_the_plugin_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            step = MemoryRunner.from_activation(
                read_step_activation(_write_payload(tmp, _valid_payload()), "memory")
            )
            shared: dict = {}
            report = step.update(
                DecisionFrameContext("frame_7", 7, 700, shared_memory=shared),
                Observation("obs_7", 690, {}),
            )
            _state(report)["records"][0]["properties"]["extra"] = 1
            _state(report)["record_count"] = 99

            self.assertEqual(shared["recording_test.state"]["records"][0].properties, {})
            self.assertEqual(_state(step.report())["record_count"], 1)
