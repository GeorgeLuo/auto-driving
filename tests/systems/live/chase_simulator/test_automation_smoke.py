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
            simulator = next(vehicle for vehicle in vehicles
                             if vehicle["provider"] == "chase-sim" and vehicle["state"] == "running")
            _assert_passive_preserved()
            cli("hosts", "create", "--id", "chase-sim-chaser", "--provider", "chase-sim",
                "--url", simulator["url"])
            try:
                cli("hosts", "stage", "perception", "--id", "chase-sim-chaser",
                    "--preset", "lightweight_observer", "--timeout-s", "6")
                try:
                    run = cli("hosts", "run", "--id", "chase-sim-chaser", "--observe-only",
                              "--num-decisions", "0", "--open-view", "--timeout-s", "6")
                    self.assertIn("Run started on host", run.stdout)
                    self.assertIn("Runtime view: http://127.0.0.1:", run.stdout)

                    host = json.loads(cli("hosts", "status", "--id", "chase-sim-chaser", "--json").stdout)["hosts"][0]["run"]
                    self.assertTrue(host["process"]["running"])
                    self.assertTrue(host["published_view"]["available"])
                    self.assertEqual(host["state"]["action_policy"], "observe_only")
                    self.assertEqual(host["state"]["control_application"], "not_applied")
                    _assert_passive_preserved()
                finally:
                    cli("hosts", "stop", "--id", "chase-sim-chaser")

                host = json.loads(cli("hosts", "status", "--id", "chase-sim-chaser", "--json").stdout)["hosts"][0]["run"]
                self.assertFalse(host["process"]["running"])
                self.assertFalse(host["published_view"]["available"])
                _assert_passive_preserved()
            finally:
                cli("hosts", "delete", "--id", "chase-sim-chaser", check=False)



if __name__ == "__main__":
    unittest.main(verbosity=2)
