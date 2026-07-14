from __future__ import annotations

import json
import unittest

from tests.support.cli_runner import run_automa


class VehicleDiscoveryTests(unittest.TestCase):
    def test_scenario_first_time_discovery_can_return_machine_readable_empty_snapshot(self) -> None:
        result = run_automa("vehicles", "active", "--no-picar", "--no-sim", "--json")

        payload = json.loads(result.stdout)
        self.assertEqual(payload["schema"], "automa_vehicle_discovery_v0")
        self.assertEqual(payload["active_count"], 0)
        self.assertEqual(payload["vehicles"], [])
        self.assertEqual(payload["discovery"]["candidate_count"], 0)
        self.assertEqual(payload["inactive"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
