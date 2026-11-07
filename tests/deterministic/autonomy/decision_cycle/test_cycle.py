from __future__ import annotations

import unittest

from dataclasses import replace

from autonomy.decision_cycle.observation.publication import OBSERVATION_KEY
from autonomy.decision_cycle.cycle import DecisionCycle, DecisionSteps
from autonomy.decision_cycle.memory.errors import MemoryUpdateError
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.perception.interface import (
    PERCEPTION_TEXT_SCHEMA,
    PerceptionText,
)
from autonomy.runtime.control import AutonomyControl
from autonomy.decision_cycle.steps import decision_steps, applied_step_activations
from tests.support.action_fixtures import fixed_action_runner, plan_runner, proposal_runner


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
            signals=(),
            things=(),
            limits=("test evidence only",),
        )

    def test_observe_only_cycle_returns_observation_and_idle(self) -> None:
        def observe(context, perception):
            self.assertIsNone(perception)
            return Observation(
                observation_id=context.frame_id,
                created_at_ms=456,
                sensor_frame={},
                summary=("custom observation",),
            )

        result = DecisionCycle(DecisionSteps(observation=observe)).run(self.context())

        self.assertIsNotNone(result.observation)
        self.assertEqual(result.observation.observation_id, "frame_000")
        self.assertEqual(result.control.reason, "decision-cycle-idle")

    def test_absent_steps_record_none_and_the_cycle_idles(self) -> None:
        perception = self.perception()
        for steps in (DecisionSteps(), DecisionSteps(perception=lambda context: perception)):
            with self.subTest(perception=steps.perception is not None):
                result = DecisionCycle(steps).run(self.context())

                self.assertIs(result.perception, None if steps.perception is None else perception)
                for record in (result.observation, result.proposal, result.plan, result.action):
                    self.assertIsNone(record)
                self.assertEqual(
                    (result.control.reason, result.control.steering, result.control.throttle),
                    ("decision-cycle-idle", 0.0, 0.0),
                )

    def test_each_step_receives_the_previous_records(self) -> None:
        calls = []
        perception = self.perception()
        observation_step = decision_steps().observation
        plan_step = plan_runner()
        action_step = fixed_action_runner(AutonomyControl(confidence=1.0, reason="fixed"))

        def observe(context, received):
            calls.append(("observation", received))
            return observation_step(context, received)

        def remember(context):
            calls.append(("memory", context.shared_memory.get(OBSERVATION_KEY)))
            return None

        def propose(context):
            shared = context.shared_memory or {}
            calls.append(("proposal", shared.get(OBSERVATION_KEY)))
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
        ).run(replace(self.context(), shared_memory={}))

        self.assertEqual(
            [name for name, _ in calls],
            ["observation", "memory", "proposal", "plan", "action"],
        )
        self.assertIs(calls[0][1], perception)
        # The built-in observation step summarizes the perception it receives.
        self.assertEqual(result.observation.perception_plugin_id, "test-perception")
        self.assertEqual(result.observation.metadata["source"], "perception_summary")
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

    def test_memory_step_report_is_recorded_and_keeps_idle(self) -> None:
        report = {"schema": "memory_report", "plugins": [{"state": {"record_count": 0}}]}

        result = DecisionCycle(
            DecisionSteps(memory=lambda context: report)
        ).run(self.context())

        self.assertIs(result.memory, report)
        self.assertEqual(result.control.reason, "decision-cycle-idle")
        self.assertEqual(result.to_dict()["memory"], report)
        result.to_dict()["memory"]["plugins"][0]["state"]["record_count"] = 9
        self.assertEqual(report["plugins"][0]["state"]["record_count"], 0)

    def test_memory_callable_failures_stop_the_cycle_with_memory_diagnostics(self) -> None:
        for failure, diagnostic in (
            (ValueError("update failed"), "ValueError: update failed"),
            (MemoryUpdateError("already classified"), "already classified"),
        ):
            with self.subTest(diagnostic=diagnostic):
                calls = []

                def remember(context):
                    calls.append("memory")
                    raise failure

                def propose(context):
                    calls.append("proposal")

                cycle = DecisionCycle(DecisionSteps(memory=remember, proposal=propose))
                with self.assertRaises(MemoryUpdateError) as caught:
                    cycle.run(self.context())

                self.assertEqual(str(caught.exception), diagnostic)
                self.assertEqual(calls, ["memory"])
                if isinstance(failure, MemoryUpdateError):
                    self.assertIs(caught.exception, failure)
                else:
                    self.assertIs(caught.exception.__cause__, failure)

    def test_a_later_raise_keeps_finished_outputs_on_the_report(self) -> None:
        first = self.perception()
        second = PerceptionText(
            schema=PERCEPTION_TEXT_SCHEMA,
            plugin_id="second-perception",
            status="ok",
            signals=(),
            things=(),
            limits=("second frame",),
        )
        calls = {"count": 0}

        def perceive(_context):
            calls["count"] += 1
            return first if calls["count"] == 1 else second

        def observe(context, _perception):
            return Observation(
                observation_id=context.frame_id,
                created_at_ms=context.timestamp_ms,
                sensor_frame={},
            )

        def propose(_context):
            if calls["count"] > 1:
                raise RuntimeError("proposal failed")
            return None

        cycle = DecisionCycle(DecisionSteps(
            perception=perceive, observation=observe, proposal=propose,
        ))
        done = cycle.run(self.context())
        self.assertIs(done.perception, first)
        with self.assertRaises(RuntimeError) as raised:
            cycle.run(replace(self.context(), frame_id="frame_001", frame_index=1))
        partial = raised.exception.cycle_result
        self.assertIs(partial.perception, second)
        self.assertIs(done.perception, first)
        self.assertEqual(partial.observation.observation_id, "frame_001")
        self.assertIsNone(partial.memory)
        self.assertIsNone(partial.proposal)
        self.assertIsNone(partial.plan)
        self.assertIsNone(partial.action)
        durations = {entry["step"]: entry["duration_ms"] for entry in partial.timing["steps"]}
        self.assertIsInstance(durations["perception"], float)
        self.assertIsInstance(durations["observation"], float)
        self.assertIsNone(durations["memory"])
        self.assertIsNone(durations["proposal"])
        self.assertIsNone(durations["plan"])
        self.assertIsNone(durations["action"])

    def test_a_raised_cycle_lists_plugins_only_for_steps_that_started(self) -> None:
        class Timed:
            """A step whose runner still holds an earlier call's plugin timing."""

            def __call__(self, *args):
                return None

            def plugin_timing(self):
                return [{"plugin_id": "earlier", "duration_ms": 1.0, "error": None}]

        class Raising(Timed):
            def __call__(self, *args):
                raise RuntimeError("proposal failed")

        cycle = DecisionCycle(DecisionSteps(
            perception=Timed(), proposal=Raising(), plan=Timed(), action=Timed(),
        ))
        with self.assertRaises(RuntimeError) as raised:
            cycle.run(self.context())
        plugins = {
            entry["step"]: [plugin["plugin_id"] for plugin in entry["plugins"]]
            for entry in raised.exception.cycle_result.timing["steps"]
        }
        self.assertEqual(plugins["perception"], ["earlier"])
        self.assertEqual(plugins["proposal"], ["earlier"])
        self.assertEqual((plugins["plan"], plugins["action"]), ([], []))


class AppliedActivationTests(unittest.TestCase):
    def test_applied_export_preserves_context_and_detaches_configs(self) -> None:
        from types import SimpleNamespace
        from autonomy.decision_cycle.activation import step_activation
        from autonomy.plugins import PluginDefinition

        loaded = step_activation(
            "plan", ["pending"], {"pending": "example:Pending", "running": "example:Old"},
            {"running": {"nested": {"value": 0}}}, metadata={"preset": {"label": "test"}},
        )
        definition = PluginDefinition(
            "plan", "running", "example:Running", {"nested": {"value": 1}},
            {"source_file": "revision.py"},
        )
        runner = SimpleNamespace(activation=loaded, applied=((definition, object()),))
        payload = applied_step_activations(DecisionSteps(plan=runner))["plan"]
        self.assertEqual(payload["plugins"], ["running"])
        self.assertEqual(payload["plugin_specs"], {
            "pending": "example:Pending", "running": "revision.py:Running",
        })
        self.assertEqual(payload["plugin_configs"]["running"]["nested"]["value"], 1)
        payload["plugin_configs"]["running"]["nested"]["value"] = 99
        payload["metadata"]["preset"]["label"] = "changed"
        self.assertEqual(definition.config["nested"]["value"], 1)
        self.assertEqual(loaded.metadata["preset"]["label"], "test")
        self.assertNotIn("memory", applied_step_activations(DecisionSteps(memory=lambda context: None)))

    def test_absent_steps_export_as_none_and_empty_runners_as_disabled(self) -> None:
        """A disarmed step keeps its runner with nothing applied; it must
        record as disabled so streams and replays run it empty."""

        steps = DecisionSteps(proposal=proposal_runner({}), plan=plan_runner())

        activations = applied_step_activations(steps)

        self.assertEqual(activations["plan"]["plugins"], ["first_active"])
        self.assertEqual(activations["proposal"]["plugins"], [])
        self.assertEqual({step: value for step, value in activations.items() if step not in ("plan", "proposal")},
                         dict.fromkeys(("perception", "observation", "memory", "action")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
