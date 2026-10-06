from __future__ import annotations

import unittest

from autonomy.decision_cycle.activation import STEPS
from autonomy.decision_cycle.runner import (
    FAILURE_POLICY_FIELDS,
    FAILURE_POLICY_VALUES,
    FailurePolicy,
    StepRunner,
)
from autonomy.decision_cycle.steps import STEP_RUNNERS
from implementations.decision_cycle.catalog import packaged_activation


class FailurePolicyTests(unittest.TestCase):
    def test_policy_reports_the_shared_fields_in_order(self) -> None:
        policy = FailurePolicy(update="stop_cycle", reset="record", missing_input="invoke")

        self.assertEqual(tuple(policy.to_dict()), FAILURE_POLICY_FIELDS)
        self.assertEqual(
            policy.to_dict(),
            {"update": "stop_cycle", "reset": "record", "missing_input": "invoke"},
        )

    def test_policy_accepts_only_the_declared_values(self) -> None:
        self.assertEqual(set(FAILURE_POLICY_VALUES), set(FAILURE_POLICY_FIELDS))
        with self.assertRaisesRegex(ValueError, "update must be one of"):
            FailurePolicy(update="ignore", reset="record", missing_input="invoke")
        with self.assertRaisesRegex(ValueError, "missing_input must be one of"):
            FailurePolicy(update="stop_cycle", reset="record", missing_input="record")


class StepRunnerSurfaceTests(unittest.TestCase):
    def test_every_step_reports_the_shared_runner_surface(self) -> None:
        shared_keys = {
            "step",
            "activation",
            "available_plugins",
            "selected_plugin_ids",
            "plugin_ids",
            "plugin_report",
            "run_count",
            "failure_count",
            "last_error",
        }
        for step in STEPS:
            with self.subTest(step=step):
                runner_class = STEP_RUNNERS[step]
                self.assertTrue(issubclass(runner_class, StepRunner))
                runner = runner_class.from_activation(packaged_activation(step))
                status = runner.status()

                self.assertLessEqual(shared_keys, set(status))
                self.assertEqual(status["step"], step)
                self.assertEqual(status["plugin_ids"], list(runner.plugins))
                self.assertEqual(
                    status["plugin_report"]["applied_plugin_ids"], list(runner.plugin_ids)
                )


if __name__ == "__main__":
    unittest.main()
