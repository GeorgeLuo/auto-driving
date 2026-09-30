from __future__ import annotations

import unittest

from autonomy.decision_cycle.cycle import DecisionCycle, DecisionSteps
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.memory.errors import MemoryUpdateError
from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.memory.snapshots.values import (
    MemoryBounds,
    empty_memory_snapshot,
)
from autonomy.decision_cycle.perception.interface import (
    PERCEPTION_TEXT_SCHEMA,
    PerceptionText,
)
from autonomy.runtime import AutonomyControl
from tests.support.action_fixtures import fixed_control_composition


class DecisionCycleTests(unittest.TestCase):
    def context(self) -> DecisionFrameContext:
        return DecisionFrameContext(
            frame_id="frame_000",
            frame_index=0,
            timestamp_ms=123,
        )

    def perception(self) -> PerceptionText:
        return PerceptionText(
            schema=PERCEPTION_TEXT_SCHEMA,
            plugin_id="test-perception",
            status="ok",
            lines=("signal id=path_clear value=true",),
            signals=(),
            things=(),
            limits=("test evidence only",),
        )

    def test_empty_cycle_returns_idle_without_observation(self) -> None:
        result = DecisionCycle().run(self.context())

        self.assertEqual(result.control.reason, "decision-cycle-idle")
        self.assertEqual(result.control.throttle, 0.0)
        self.assertEqual(result.control.steering, 0.0)
        self.assertIsNone(result.perception)
        self.assertIsNone(result.observation)

    def test_observe_only_cycle_returns_observation_and_idle(self) -> None:
        def observe(context, perception):
            self.assertIsNone(perception)
            return Observation(
                observation_id=context.frame_id,
                created_at_ms=456,
                sensor_snapshot={},
                summary=("custom observation",),
            )

        result = DecisionCycle(DecisionSteps(observe=observe)).run(self.context())

        self.assertIsNotNone(result.observation)
        self.assertEqual(result.observation.observation_id, "frame_000")
        self.assertEqual(result.control.reason, "decision-cycle-idle")

    def test_perception_without_observe_step_uses_default_observation(self) -> None:
        perception = self.perception()

        result = DecisionCycle(
            DecisionSteps(perceive=lambda context: perception),
        ).run(self.context())

        self.assertIs(result.perception, perception)
        self.assertIsNotNone(result.observation)
        self.assertEqual(result.observation.observation_id, "frame_000")
        self.assertEqual(result.observation.perception_plugin_id, "test-perception")
        self.assertEqual(result.observation.metadata["source"], "default_observe_step")
        self.assertEqual(result.control.reason, "decision-cycle-idle")

    def test_action_only_cycle_uses_action_output(self) -> None:
        composition = fixed_control_composition(
            AutonomyControl(
                steering=0.25,
                throttle=0.0,
                confidence=0.8,
                reason="test-action",
            )
        )

        def act(context, perception, observation, memory):
            self.assertEqual(context.frame_id, "frame_000")
            self.assertIsNone(perception)
            self.assertIsNone(observation)
            self.assertIsNone(memory)
            return composition.act(context, perception, observation, memory)

        result = DecisionCycle(DecisionSteps(act=act)).run(self.context())

        self.assertEqual(result.control.reason, "test-action")
        self.assertEqual(result.control.steering, 0.25)
        self.assertEqual(result.control.confidence, 0.8)
        self.assertEqual(result.action.authority.gate_id, "test")
        self.assertFalse(result.action.authority.proposed_applied)
        self.assertEqual(result.to_dict()["action"]["authority"]["authorized_output"]["reason"], "test-action")

    def test_none_action_output_uses_configured_idle_control(self) -> None:
        cycle = DecisionCycle(
            DecisionSteps(act=lambda *args: None),
            idle_reason="waiting-for-decision",
        )

        result = cycle.run(self.context())

        self.assertEqual(result.control.steering, 0.0)
        self.assertEqual(result.control.throttle, 0.0)
        self.assertEqual(result.control.confidence, 1.0)
        self.assertEqual(result.control.reason, "waiting-for-decision")

    def test_action_step_rejects_undeclared_dictionary_output(self) -> None:
        def act(context, perception, observation, memory):
            return {"steering": 0.0, "throttle": 0.0}

        cycle = DecisionCycle(DecisionSteps(act=act))

        with self.assertRaisesRegex(TypeError, "must return ActionResult or None"):
            cycle.run(self.context())

    def test_memory_step_accepts_typed_snapshot_and_keeps_idle(self) -> None:
        snapshot = empty_memory_snapshot(
            memory_id="mem_frame_000",
            epoch_id="epoch_1",
            bounds=MemoryBounds(max_records=4),
            created_at_ms=10,
            implementation_id="test",
        )

        result = DecisionCycle(
            DecisionSteps(remember=lambda context, observation: snapshot)
        ).run(self.context())

        self.assertIs(result.memory, snapshot)
        self.assertEqual(result.memory.health, "empty")
        self.assertEqual(result.control.reason, "decision-cycle-idle")
        self.assertEqual(result.to_dict()["memory"]["health"], "empty")

    def test_memory_step_rejects_undeclared_dictionary_output(self) -> None:
        cycle = DecisionCycle(
            DecisionSteps(remember=lambda context, observation: {"records": []})
        )

        with self.assertRaisesRegex(MemoryUpdateError, "must return MemorySnapshot or None"):
            cycle.run(self.context())

    def test_failed_memory_update_stops_action_without_rewriting_plugin_memory(self) -> None:
        shared_memory = {}
        actions = []

        def remember(context, observation):
            context.shared_memory["test.plugin_write"] = "retained"
            raise RuntimeError("update failed")

        cycle = DecisionCycle(
            DecisionSteps(
                remember=remember,
                act=lambda *args: actions.append(args),
            )
        )
        context = DecisionFrameContext(
            frame_id="frame_001",
            frame_index=1,
            timestamp_ms=123,
            shared_memory=shared_memory,
        )

        with self.assertRaisesRegex(MemoryUpdateError, "update failed"):
            cycle.run(context)
        self.assertEqual(shared_memory, {"test.plugin_write": "retained"})
        self.assertEqual(actions, [])

    def test_unprintable_memory_error_keeps_memory_failure_boundary(self) -> None:
        class UnprintableError(Exception):
            def __str__(self):
                raise RuntimeError("cannot format")

        def remember(context, observation):
            raise UnprintableError()

        cycle = DecisionCycle(DecisionSteps(remember=remember))
        with self.assertRaisesRegex(MemoryUpdateError, "unprintable error"):
            cycle.run(self.context())


if __name__ == "__main__":
    unittest.main(verbosity=2)
