from __future__ import annotations

import json
import unittest

from tests.shared.cli.invoke import invoke_automa


class SimulatorStatusTests(unittest.TestCase):
    def test_missing_simeval_status_is_structured_and_actionable(self) -> None:
        env = {"PATH": "", "AUTOMA_SIMEVAL_BIN": ""}
        human = invoke_automa(
            "simulators",
            "status",
            extra_env=env,
            check=False,
        )
        machine = invoke_automa(
            "simulators",
            "status",
            "--json",
            extra_env=env,
            check=False,
        )

        payload = json.loads(machine.stdout)
        result = payload["result"]
        self.assertEqual(human.returncode, 2)
        self.assertEqual(machine.returncode, 2)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason_code"], "simeval_missing")
        self.assertIn(result["error"], human.stdout)
        self.assertIn(result["recovery"], human.stdout)
        self.assertEqual(payload["commands"], [])
        self.assertNotIn("Traceback", human.stdout)
        self.assertEqual(human.stderr, "")
        self.assertEqual(machine.stderr, "")


class SimulatorEnsureTests(unittest.TestCase):
    def test_missing_simeval_ensure_is_structured_and_actionable(self) -> None:
        env = {"PATH": "", "AUTOMA_SIMEVAL_BIN": ""}
        human = invoke_automa(
            "simulators",
            "ensure",
            extra_env=env,
            check=False,
        )
        machine = invoke_automa(
            "simulators",
            "ensure",
            "--json",
            extra_env=env,
            check=False,
        )

        payload = json.loads(machine.stdout)
        result = payload["result"]
        self.assertEqual(human.returncode, 2)
        self.assertEqual(machine.returncode, 2)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason_code"], "simeval_missing")
        self.assertFalse(result["launch_attempted"])
        self.assertIn(result["error"], human.stdout)
        self.assertIn(result["recovery"], human.stdout)
        self.assertEqual(payload["commands"], [])
        self.assertNotIn("Traceback", human.stdout)
        self.assertEqual(human.stderr, "")
        self.assertEqual(machine.stderr, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
