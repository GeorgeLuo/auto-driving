"""The Chase adapter: simulator captures into the shared loop behind the shared routes."""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from autonomy.runtime.client import RuntimeClient
from autonomy.runtime.session import RunConfiguration
from cli.automa_cli.bundles import controller_bundle_paths
from implementations.runtime.chase_sim.service import ChaseRuntimeHost
from tests.shared.cli.automation_pipeline.pipeline_fixtures import _FakeCar, _write_activations
from tests.shared.implementations.runtime.wait import wait_until as _wait


class ChaseRuntimeHostTests(unittest.TestCase):
    def test_a_bounded_session_captures_only_until_its_last_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = controller_bundle_paths(Path(tmp) / "chase-sim-chaser")
            _write_activations(bundle)
            car = _FakeCar()
            with ChaseRuntimeHost(car=car, runtime_dir=Path(bundle["runtime_dir"]),
                                  vehicle_id="chase-sim-chaser") as host:
                client = RuntimeClient(host.base_url, timeout_s=2.0)
                client.start(RunConfiguration(mode="observe_only", num_decisions=2))
                session = _wait(
                    lambda: (s := client.status()["session"])["status"] != "running" and s,
                    message="the session did not complete",
                )
                # A read already inside the car can finish after the session
                # leaves running. Allow that one read, then hold.
                captured = car.capture_count
                time.sleep(0.3)
                observation = client.host_status()["autonomy"]["components"]["observation"]
                decision = client.request("GET", "/autonomy/decision/latest")

        self.assertEqual((session["status"], session["processed_decisions"]), ("completed", 2))
        self.assertLessEqual(car.capture_count, captured + 1, "the host captured after its session ended")
        self.assertEqual(observation["latest"]["mode"], "observe_only")
        self.assertFalse(observation["latest"]["control"]["applied"])
        self.assertEqual(decision["status"], "ready", decision)


if __name__ == "__main__":
    unittest.main()
