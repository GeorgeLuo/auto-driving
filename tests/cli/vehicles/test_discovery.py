from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from cli.automa_cli.vehicles import Candidate, _probe_picar, format_active_vehicles_snapshot
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

    def test_picar_probe_distinguishes_a_reachable_host_without_a_server(self) -> None:
        with (
            patch(
                "cli.automa_cli.vehicles._get_json",
                return_value=(None, "GET status failed"),
            ),
            patch(
                "cli.automa_cli.vehicles._probe_tcp_endpoint",
                return_value={
                    "runtime_state": "server_not_listening",
                    "tcp_listener": False,
                    "http_ready": False,
                    "tcp_address": "192.168.0.168",
                    "tcp_error": "connection refused",
                },
            ),
        ):
            result = _probe_picar(
                Candidate("picar", "http://piracer.local:8887", "default"),
                timeout_s=0.1,
            )

        self.assertFalse(result.active)
        self.assertIn("server is not listening", result.error or "")
        self.assertEqual(result.diagnostics["runtime_state"], "server_not_listening")
        snapshot = format_active_vehicles_snapshot(
            {
                "active_count": 0,
                "vehicles": [],
                "inactive": [result.to_dict()],
            },
            include_inactive=True,
        )
        self.assertIn("runtime=server_not_listening", snapshot)
        self.assertIn("tcp=no", snapshot)
        self.assertIn("http=no", snapshot)

    def test_picar_probe_reports_listener_without_http_readiness(self) -> None:
        with (
            patch(
                "cli.automa_cli.vehicles._get_json",
                return_value=(None, "GET status timed out"),
            ),
            patch(
                "cli.automa_cli.vehicles._probe_tcp_endpoint",
                return_value={
                    "runtime_state": "http_unhealthy",
                    "tcp_listener": True,
                    "http_ready": False,
                },
            ),
        ):
            result = _probe_picar(
                Candidate("picar", "http://piracer.local:8887", "default"),
                timeout_s=0.1,
            )

        self.assertFalse(result.active)
        self.assertIn("TCP listener is reachable", result.error or "")
        self.assertEqual(result.diagnostics["runtime_state"], "http_unhealthy")


if __name__ == "__main__":
    unittest.main(verbosity=2)
