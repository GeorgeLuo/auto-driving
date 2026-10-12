"""Offline proposal, plan, and action inspect over images and recorded runs."""
from __future__ import annotations

import unittest

from autonomy.decision_cycle.activation import DECISION_STEPS
from tests.support.cli_runner import run_automa


class DecisionStepInspectTests(unittest.TestCase):
    def test_cli_has_a_group_per_step_and_no_decision_command(self) -> None:
        for step in DECISION_STEPS:
            with self.subTest(step=step):
                flags = run_automa("vehicles", step, "inspect", "--help").stdout
                for flag in ("--plugin", "--frame", "--record", "--json", "--max-frames"):
                    self.assertIn(flag, flags)
        removed = run_automa("vehicles", "decision", "help", check=False)
        self.assertEqual(removed.returncode, 2)
        self.assertIn("invalid choice: 'decision'", removed.stderr)


if __name__ == "__main__":
    unittest.main()
