"""A changed selection takes effect only when it is applied, for every step."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.perception.feeds.context import PerceptionRequest
from autonomy.decision_cycle.steps import decision_steps
from autonomy.plugins import PluginDefinition
from autonomy.runtime.plugin_loader import INSTALLED_PACKAGE
from autonomy.vehicle import SensorFrame
from implementations.decision_cycle.catalog import packaged_activation
from tests.shared.integration.plugin_flow_fixtures import write_plugin

CALLS = {
    "perception": lambda runner, shared: runner.perceive(PerceptionRequest(SensorFrame(
        read_id="frame", readings={}, started_at_ms=100, completed_at_ms=100,
    ), shared_memory=shared)),
    "memory": lambda runner, shared: runner.update(
        DecisionFrameContext("frame", 0, 100, shared_memory=shared), None
    ),
    "proposal": lambda runner, shared: runner.run(
        frame_id="frame", frame_index=0, timestamp_ms=100, shared_memory=shared,
    ),
    "plan": lambda runner, shared: runner(
        DecisionFrameContext("frame", 0, 100, shared_memory=shared), None
    ),
}


class RunnerSelectionTests(unittest.TestCase):
    def test_a_step_call_runs_the_applied_plugins_until_the_selection_is_applied(self) -> None:
        """Regression: perception and memory applied a changed selection on
        their next call and raised when it failed; proposal applied it and
        swallowed the failure. A running host now decides both."""

        steps = decision_steps(
            {step: packaged_activation(step) for step in CALLS}, source=INSTALLED_PACKAGE
        )
        with tempfile.TemporaryDirectory() as directory:
            plan_file = Path(directory) / "plan.py"
            write_plugin(plan_file, "plan", "prototype-plan", 1)
            plan_definition = PluginDefinition(
                step="plan", plugin_id="prototype-plan", entrypoint=f"{plan_file}:Prototype",
            )
            for step, call in CALLS.items():
                with self.subTest(step=step):
                    runner = getattr(steps, step)
                    applied = runner.plugin_ids
                    self.assertTrue(applied)
                    shared: dict = {}
                    if runner.single_plugin:
                        runner.plugin_manager.select([plan_definition])
                        published = ("prototype-plan",)
                    else:
                        runner.plugin_manager.select([])
                        published = ()
                    call(runner, shared)
                    self.assertEqual(runner.plugin_ids, applied)
                    runner.apply_selection(shared)
                    self.assertEqual(runner.plugin_ids, published)


if __name__ == "__main__":
    unittest.main()
