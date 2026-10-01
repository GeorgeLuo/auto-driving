from __future__ import annotations

import unittest

from autonomy.decision_cycle.cycle import DecisionCycle, DecisionSteps
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.perception.interface import (
    PERCEPTION_TEXT_SCHEMA,
    PerceptionText,
)
from autonomy.runtime.control import AutonomyControl
from autonomy.decision_cycle.steps import decision_steps
from tests.support.action_fixtures import fixed_action_runner, plan_runner


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

        result = DecisionCycle(DecisionSteps(observation=observe)).run(self.context())

        self.assertIsNotNone(result.observation)
        self.assertEqual(result.observation.observation_id, "frame_000")
        self.assertEqual(result.control.reason, "decision-cycle-idle")

    def test_builtin_observation_step_records_perception(self) -> None:
        perception = self.perception()

        result = DecisionCycle(
            DecisionSteps(
                perception=lambda context: perception,
                observation=decision_steps().observation,
            ),
        ).run(self.context())

        self.assertIs(result.perception, perception)
        self.assertIsNotNone(result.observation)
        self.assertEqual(result.observation.observation_id, "frame_000")
        self.assertEqual(result.observation.perception_plugin_id, "test-perception")
        self.assertEqual(result.observation.metadata["source"], "perception_summary")
        self.assertEqual(result.control.reason, "decision-cycle-idle")

    def test_empty_step_records_none(self) -> None:
        result = DecisionCycle(
            DecisionSteps(perception=lambda context: self.perception()),
        ).run(self.context())

        self.assertIsNotNone(result.perception)
        self.assertIsNone(result.observation)
        self.assertIsNone(result.proposal)
        self.assertIsNone(result.plan)
        self.assertIsNone(result.action)

    def test_each_step_receives_the_previous_records(self) -> None:
        calls = []
        perception = self.perception()
        observation_step = decision_steps().observation
        plan_step = plan_runner()
        action_step = fixed_action_runner(AutonomyControl(confidence=1.0, reason="fixed"))

        def observe(context, received):
            calls.append(("observation", received))
            return observation_step(context, received)

        def remember(context, observation):
            calls.append(("memory", observation))
            return None

        def propose(context, observation):
            calls.append(("proposal", observation))
            return None

        def plan(context, proposal):
            calls.append(("plan", proposal))
            return plan_step(context, proposal)

        def act(context, proposal, plan):
            calls.append(("action", plan))
            return action_step(context, proposal, plan)

        result = DecisionCycle(
            DecisionSteps(
                perception=lambda context: perception,
                observation=observe,
                memory=remember,
                proposal=propose,
                plan=plan,
                action=act,
            )
        ).run(self.context())

        self.assertEqual(
            [name for name, _ in calls],
            ["observation", "memory", "proposal", "plan", "action"],
        )
        self.assertIs(calls[0][1], perception)
        self.assertIs(calls[1][1], result.observation)
        self.assertIs(calls[2][1], result.observation)
        self.assertIsNone(calls[3][1])
        self.assertIs(calls[4][1], result.plan)
        self.assertEqual(result.plan.status, "idle")
        self.assertEqual(result.control.reason, "fixed")

    def test_action_only_cycle_uses_action_output(self) -> None:
        action = fixed_action_runner(
            AutonomyControl(
                steering=0.25,
                throttle=0.0,
                confidence=0.8,
                reason="test-action",
            )
        )

        result = DecisionCycle(DecisionSteps(plan=plan_runner(), action=action)).run(
            self.context()
        )

        self.assertEqual(result.control.reason, "test-action")
        self.assertEqual(result.control.steering, 0.25)
        self.assertEqual(result.control.confidence, 0.8)
        self.assertEqual(result.action.authority.gate_id, "test")
        self.assertFalse(result.action.authority.proposed_applied)
        self.assertEqual(result.to_dict()["action"]["authority"]["authorized_output"]["reason"], "test-action")

    def test_none_action_output_uses_configured_idle_control(self) -> None:
        cycle = DecisionCycle(
            DecisionSteps(action=lambda *args: None),
            idle_reason="waiting-for-decision",
        )

        result = cycle.run(self.context())

        self.assertEqual(result.control.steering, 0.0)
        self.assertEqual(result.control.throttle, 0.0)
        self.assertEqual(result.control.confidence, 1.0)
        self.assertEqual(result.control.reason, "waiting-for-decision")


    def test_memory_step_report_is_recorded_and_keeps_idle(self) -> None:
        report = {"schema": "memory_report_v0", "plugins": [{"state": {"record_count": 0}}]}

        result = DecisionCycle(
            DecisionSteps(memory=lambda context, observation: report)
        ).run(self.context())

        self.assertIs(result.memory, report)
        self.assertEqual(result.control.reason, "decision-cycle-idle")
        self.assertEqual(result.to_dict()["memory"], report)
        result.to_dict()["memory"]["plugins"][0]["state"]["record_count"] = 9
        self.assertEqual(report["plugins"][0]["state"]["record_count"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
