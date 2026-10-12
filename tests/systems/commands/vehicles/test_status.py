from __future__ import annotations

import json
import unittest

from tests.support.cli_runner import run_automa


class VehicleStatusTests(unittest.TestCase):


    def test_conflicting_explicit_urls_exit_two_before_any_probe(self) -> None:
        result = run_automa(
            "vehicles",
            "status",
            "--chase-url",
            "http://localhost:5050",
            "--chase-ws-url",
            "ws://localhost:5050/ws/control",
            "--json",
            check=False,
        )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("cannot be used together", result.stderr)


    def test_active_json_schema_remains_compatible_while_human_copy_is_narrower(self) -> None:
        result = run_automa(
            "vehicles",
            "active",
            "--no-picar",
            "--no-sim",
            "--json",
        )
        self.assertEqual(
            json.loads(result.stdout)["schema"],
            "automa_vehicle_discovery_v0",
        )
        human = run_automa(
            "vehicles",
            "active",
            "--no-picar",
            "--no-sim",
        )
        self.assertIn("Discoverable vehicles: 0", human.stdout)
        self.assertNotIn("Active vehicles:", human.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
