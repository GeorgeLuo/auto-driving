"""Each cycle's timing: its total, each step's, and each applied plugin's."""

from __future__ import annotations

import unittest

from autonomy.decision_cycle.activation import STEPS
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.cycle import DecisionCycle
from autonomy.decision_cycle.observation.runner import ObservationRunner
from autonomy.decision_cycle.plan.runner import PlanRunner
from autonomy.decision_cycle.proposal.result import ProposalResult
from autonomy.decision_cycle.steps import decision_steps
from autonomy.runtime.plugin_loader import INSTALLED_PACKAGE
from autonomy.vehicle import SensorFrame
from implementations.decision_cycle.catalog import packaged_activation


def _context(index: int, shared_memory: dict) -> DecisionFrameContext:
    frame_id = f"frame_{index:03d}"
    return DecisionFrameContext(
        frame_id,
        index,
        100 + index,
        shared_memory=shared_memory,
        sensor_frame=SensorFrame(
            read_id=frame_id, readings={}, started_at_ms=100, completed_at_ms=100
        ),
    )


class CycleTimingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.steps = decision_steps(
            {step: packaged_activation(step) for step in STEPS}, source=INSTALLED_PACKAGE
        )
        self.shared: dict = {}

    def test_a_cycle_times_every_step_and_applied_plugin_with_one_total(self) -> None:
        result = DecisionCycle(self.steps).run(_context(0, self.shared))

        timing = result.timing
        self.assertEqual([entry["step"] for entry in timing["steps"]], list(STEPS))
        self.assertEqual(result.duration_ms, timing["duration_ms"])
        self.assertEqual(result.to_dict()["timing"], timing)
        for entry in timing["steps"]:
            with self.subTest(step=entry["step"]):
                runner = getattr(self.steps, entry["step"])
                self.assertEqual(
                    [plugin["plugin_id"] for plugin in entry["plugins"]], list(runner.plugin_ids)
                )
                self.assertGreaterEqual(timing["duration_ms"], entry["duration_ms"])
                for plugin in entry["plugins"]:
                    self.assertGreaterEqual(entry["duration_ms"], plugin["duration_ms"])
                self.assertEqual(runner.status()["last_duration_ms"], entry["duration_ms"])

    def test_a_skipped_plan_plugin_has_no_time_for_that_cycle(self) -> None:
        """Regression: a skipped plan kept the previous cycle's time and error."""

        class Raises:
            plugin_id = "raises"

            def plan(self, candidates, *, frame_id, timestamp_ms):
                raise RuntimeError("plan failed")

        plan = PlanRunner.from_plugins({"raises": Raises()})
        ok = ProposalResult(frame_id="frame_000", status="ok")
        self.assertIsNone(plan(_context(0, {}), ok))
        [ran] = plan.timing()["plugins"]
        self.assertIsInstance(ran["duration_ms"], float)
        self.assertIn("plan failed", ran["error"])

        failed = ProposalResult(frame_id="frame_001", status="error", reason="step_internal_error")
        self.assertIsNone(plan(_context(1, {}), failed))
        self.assertEqual(
            plan.timing()["plugins"],
            [{"plugin_id": "raises", "duration_ms": None, "error": None}],
        )
        self.assertIsInstance(plan.timing()["duration_ms"], float)

    def test_a_raising_plugin_keeps_its_time_and_error(self) -> None:
        class Raises:
            plugin_id = "raises"

            def observe(self, context, perception):
                raise RuntimeError("observation failed")

        runner = ObservationRunner.from_plugins({"raises": Raises()})
        with self.assertRaises(RuntimeError):
            runner(_context(0, {}), None)

        timing = runner.timing()
        self.assertIsInstance(timing["duration_ms"], float)
        [plugin] = timing["plugins"]
        self.assertEqual(plugin["plugin_id"], "raises")
        self.assertIsInstance(plugin["duration_ms"], float)
        self.assertIn("observation failed", plugin["error"])

    def test_reset_clears_cycle_times_and_memory_reports_its_reset(self) -> None:
        DecisionCycle(self.steps).run(_context(0, self.shared))

        for step in ("perception", "proposal"):
            with self.subTest(step=step):
                runner = getattr(self.steps, step)
                runner.reset(self.shared)
                self.assertEqual(
                    runner.timing()["plugins"],
                    [
                        {"plugin_id": plugin_id, "duration_ms": None, "error": None}
                        for plugin_id in runner.plugin_ids
                    ],
                )
                self.assertIsNone(runner.timing()["duration_ms"])

        self.steps.memory.reset(self.shared)
        memory = self.steps.memory.timing()
        self.assertIsInstance(memory["duration_ms"], float)
        self.assertEqual(
            [plugin["plugin_id"] for plugin in memory["plugins"]],
            list(self.steps.memory.plugin_ids),
        )
        for plugin in memory["plugins"]:
            self.assertIsInstance(plugin["duration_ms"], float)


if __name__ == "__main__":
    unittest.main()
