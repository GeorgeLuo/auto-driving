"""A constructed runner keeps its plugins for every later call."""

from __future__ import annotations

import unittest

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.perception.feeds.context import PerceptionRequest
from autonomy.decision_cycle.steps import decision_steps
from autonomy.runtime.plugin_loader import INSTALLED_PACKAGE
from autonomy.vehicle import SensorFrame
from implementations.decision_cycle.catalog import packaged_activation

CALLS = {
    "perception": lambda runner, shared: runner.perceive(PerceptionRequest(SensorFrame(
        read_id="frame", readings={}, started_at_ms=100, completed_at_ms=100,
    ), shared_memory=shared)),
    "memory": lambda runner, shared: runner.update(DecisionFrameContext("frame", 0, 100, shared_memory=shared)),
    "proposal": lambda runner, shared: runner(DecisionFrameContext(frame_id="frame", frame_index=0, timestamp_ms=100, shared_memory=shared)),
    "plan": lambda runner, shared: runner(
        DecisionFrameContext("frame", 0, 100, shared_memory=shared), None
    ),
}


class RunnerSelectionTests(unittest.TestCase):
    def test_a_step_call_keeps_the_constructed_plugins(self) -> None:
        steps = decision_steps(
            {step: packaged_activation(step) for step in CALLS}, source=INSTALLED_PACKAGE
        )
        for step, call in CALLS.items():
            with self.subTest(step=step):
                runner = getattr(steps, step)
                applied = runner.plugin_ids
                plugin = next(iter(runner.plugins.values()))
                self.assertTrue(applied)
                call(runner, {})
                call(runner, {})
                self.assertEqual(runner.plugin_ids, applied)
                self.assertIs(next(iter(runner.plugins.values())), plugin)


if __name__ == "__main__":
    unittest.main()
