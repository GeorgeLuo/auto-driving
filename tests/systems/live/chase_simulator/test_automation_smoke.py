from __future__ import annotations

import json
import os
import unittest

from tests.support.cli_runner import run_automa


def _assert_passive_preserved(payload: dict) -> None:
    passive = payload["layers"]["passive_capture"]
    preservation = passive["session_preservation"]
    if passive["state"] != "available" or preservation["preserved"] is not True:
        raise AssertionError(f"passive session is not proven: {passive}")
    if passive.get("mutation_attempted") is not False:
        raise AssertionError(f"passive capture attempted a mutation: {passive}")


class ChaseSimulatorSmokeTests(unittest.TestCase):
    @unittest.skipUnless(
        os.environ.get("AUTOMA_TEST_LIVE_SIM") == "1",
        "set AUTOMA_TEST_LIVE_SIM=1 to run live simulator integration",
    )
    def test_scenario_live_simulator_bounded_automation_smoke(self) -> None:
        initial = run_automa(
            "vehicles",
            "status",
            "--id",
            "chase-sim-chaser",
            "--chase-url",
            "http://localhost:5050",
            "--json",
        )
        _assert_passive_preserved(json.loads(initial.stdout))

        run_automa(
            "vehicles",
            "update",
            "perception",
            "--id",
            "chase-sim-chaser",
            "--preset",
            "lightweight_observer",
            "--timeout-s",
            "6",
        )
        try:
            run = run_automa(
                "vehicles",
                "host",
                "run",
                "--id",
                "chase-sim-chaser",
                "--observe-only",
                "--num-decisions",
                "0",
                "--open-view",
                "--timeout-s",
                "6",
            )
            self.assertIn("Run started on host", run.stdout)
            self.assertIn("Runtime view: http://127.0.0.1:", run.stdout)

            status = run_automa(
                "vehicles",
                "status",
                "--id",
                "chase-sim-chaser",
                "--json",
            )
            payload = json.loads(status.stdout)
            self.assertEqual(
                payload["layers"]["automation_worker"]["state"],
                "running",
            )
            self.assertEqual(
                payload["layers"]["perception_view"]["state"],
                "available",
            )
            authority = payload["layers"]["automation_worker"]["details"]["authority"]
            self.assertEqual(authority["action_policy"], "observe_only")
            self.assertEqual(authority["control_application"], "not_applied")
            _assert_passive_preserved(payload)
        finally:
            run_automa(
                "vehicles",
                "host",
                "stop",
                "--id",
                "chase-sim-chaser",
            )

        stopped = json.loads(
            run_automa(
                "vehicles",
                "status",
                "--id",
                "chase-sim-chaser",
                "--json",
            ).stdout
        )
        self.assertEqual(
            stopped["layers"]["automation_worker"]["state"],
            "stopped",
        )
        self.assertNotEqual(
            stopped["layers"]["perception_view"]["state"],
            "available",
        )
        _assert_passive_preserved(stopped)


if __name__ == "__main__":
    unittest.main(verbosity=2)
