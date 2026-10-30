from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from implementations.vehicle.chase_sim.car import ChaseSimCar
from tests.support.cli_runner import run_automa


def _assert_passive_preserved() -> None:
    passive = ChaseSimCar().inspect_passive_capture()
    if passive["status"] != "available" or passive["session_preservation"]["preserved"] is not True:
        raise AssertionError(f"passive session is not proven: {passive}")
    if passive.get("mutation_attempted") is not False:
        raise AssertionError(f"passive capture attempted a mutation: {passive}")


class ChaseSimulatorSmokeTests(unittest.TestCase):
    @unittest.skipUnless(
        os.environ.get("AUTOMA_TEST_LIVE_SIM") == "1",
        "set AUTOMA_TEST_LIVE_SIM=1 to run live simulator integration",
    )
    def test_scenario_live_simulator_bounded_automation_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime_root = Path(directory)

            def cli(*args: str, check: bool = True):
                return run_automa(*args, runtime_root=runtime_root, check=check)

            vehicles = json.loads(cli("vehicles", "status", "--json").stdout)["vehicles"]
            simulator = next((vehicle for vehicle in vehicles
                              if vehicle["provider"] == "chase-sim" and vehicle["state"] == "running"), None)
            if simulator is None:
                self.fail("no Chase simulator runs at CHASE_UI_WS_URL; set it to the URL simulators create printed")
            _assert_passive_preserved()
            cli("hosts", "create", "--name", "chase-sim-chaser", "--provider", "chase-sim",
                "--url", simulator["url"])
            try:
                try:
                    run = cli("hosts", "run", "--name", "chase-sim-chaser", "--observe-only",
                              "--open-view", "--timeout-s", "6")
                    self.assertIn("Run started on host", run.stdout)
                    self.assertIn("Runtime view: http://127.0.0.1:", run.stdout)

                    host = json.loads(cli("hosts", "status", "--name", "chase-sim-chaser", "--json").stdout)["hosts"][0]["run"]
                    self.assertTrue(host["process"]["running"])
                    self.assertTrue(host["published_view"]["available"])
                    self.assertEqual(host["state"]["action_policy"], "observe_only")
                    self.assertEqual(host["state"]["control_application"], "not_applied")
                    _assert_passive_preserved()
                finally:
                    cli("hosts", "stop", "--name", "chase-sim-chaser")

                host = json.loads(cli("hosts", "status", "--name", "chase-sim-chaser", "--json").stdout)["hosts"][0]["run"]
                self.assertFalse(host["process"]["running"])
                self.assertFalse(host["published_view"]["available"])
                _assert_passive_preserved()
            finally:
                cli("hosts", "delete", "--name", "chase-sim-chaser", check=False)



if __name__ == "__main__":
    unittest.main(verbosity=2)
