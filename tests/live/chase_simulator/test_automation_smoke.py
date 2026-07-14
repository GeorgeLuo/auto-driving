from __future__ import annotations

import json
import os
import unittest

from tests.support.cli_runner import run_automa


class ChaseSimulatorSmokeTests(unittest.TestCase):
    @unittest.skipUnless(
        os.environ.get("AUTOMA_TEST_LIVE_SIM") == "1",
        "set AUTOMA_TEST_LIVE_SIM=1 to run live simulator integration",
    )
    def test_scenario_live_simulator_bounded_automation_smoke(self) -> None:
        run = run_automa(
            "vehicles",
            "automation",
            "run",
            "--id",
            "chase-sim-chaser",
            "--frames",
            "1",
            "--interval-s",
            "0",
            "--timeout-s",
            "6",
        )
        self.assertIn("Log: disabled", run.stdout)

        status = run_automa("vehicles", "automation", "status", "--id", "chase-sim-chaser", "--json")
        payload = json.loads(status.stdout)
        self.assertEqual(payload["vehicles"][0]["state"]["max_frames"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
