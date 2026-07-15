from __future__ import annotations

import unittest

from autonomy.decision import (
    DecisionCycle,
    DecisionFrameContext,
    DecisionStages,
    Observation,
)
from autonomy.perception import PERCEPTION_TEXT_SCHEMA, PerceptionText
from autonomy.runtime import AutonomyControl


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

        result = DecisionCycle(DecisionStages(observe=observe)).run(self.context())

        self.assertIsNotNone(result.observation)
        self.assertEqual(result.observation.observation_id, "frame_000")
        self.assertEqual(result.control.reason, "decision-cycle-idle")

    def test_perception_without_observe_stage_uses_default_observation(self) -> None:
        perception = self.perception()

        result = DecisionCycle(
            DecisionStages(perceive=lambda context: perception),
        ).run(self.context())

        self.assertIs(result.perception, perception)
        self.assertIsNotNone(result.observation)
        self.assertEqual(result.observation.observation_id, "frame_000")
        self.assertEqual(result.observation.perception_plugin_id, "test-perception")
        self.assertEqual(result.observation.metadata["source"], "default_observe_stage")
        self.assertEqual(result.control.reason, "decision-cycle-idle")

    def test_action_only_cycle_uses_action_output(self) -> None:
        def choose_action(context, perception, observation, memory, patterns, projections):
            self.assertEqual(context.frame_id, "frame_000")
            self.assertIsNone(perception)
            self.assertIsNone(observation)
            self.assertIsNone(memory)
            self.assertIsNone(patterns)
            self.assertIsNone(projections)
            return AutonomyControl(
                steering=0.25,
                throttle=0.0,
                confidence=0.8,
                reason="test-action",
            )

        result = DecisionCycle(DecisionStages(choose_action=choose_action)).run(self.context())

        self.assertEqual(result.control.reason, "test-action")
        self.assertEqual(result.control.steering, 0.25)
        self.assertEqual(result.control.confidence, 0.8)

    def test_none_action_output_uses_configured_idle_control(self) -> None:
        cycle = DecisionCycle(
            DecisionStages(choose_action=lambda *args: None),
            idle_reason="waiting-for-decision",
        )

        result = cycle.run(self.context())

        self.assertEqual(result.control.steering, 0.0)
        self.assertEqual(result.control.throttle, 0.0)
        self.assertEqual(result.control.confidence, 1.0)
        self.assertEqual(result.control.reason, "waiting-for-decision")

    def test_action_stage_rejects_undeclared_dictionary_output(self) -> None:
        def choose_action(context, perception, observation, memory, patterns, projections):
            return {"steering": 0.0, "throttle": 0.0}

        cycle = DecisionCycle(DecisionStages(choose_action=choose_action))

        with self.assertRaisesRegex(TypeError, "must return AutonomyControl or None"):
            cycle.run(self.context())

if __name__ == "__main__":
    unittest.main(verbosity=2)
