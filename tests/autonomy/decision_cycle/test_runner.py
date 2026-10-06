from __future__ import annotations

import unittest

from autonomy.decision_cycle.runner import (
    FAILURE_POLICY_FIELDS,
    FAILURE_POLICY_VALUES,
    FailurePolicy,
)


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


if __name__ == "__main__":
    unittest.main()
