from __future__ import annotations

import unittest
from unittest.mock import patch

from cli.automa_cli.vehicles.probes import (
    Candidate,
    probe_picar,
)
from tests.shared.cli.invoke import invoke_automa


class VehicleDiscoveryTests(unittest.TestCase):
    def test_picar_probe_distinguishes_a_reachable_host_without_a_server(self) -> None:
        with (
            patch(
                "cli.automa_cli.vehicles.probes._get_json",
                return_value=(None, "GET status failed"),
            ),
            patch(
                "cli.automa_cli.vehicles.probes._probe_tcp_endpoint",
                return_value={
                    "runtime_state": "server_not_listening",
                    "tcp_listener": False,
                    "http_ready": False,
                    "tcp_address": "192.168.0.168",
                    "tcp_error": "connection refused",
                },
            ),
        ):
            result = probe_picar(
                Candidate("picar", "http://piracer.local:8887", "default"),
                timeout_s=0.1,
            )

        self.assertFalse(result.active)
        self.assertIn("server is not listening", result.error or "")
        self.assertEqual(result.diagnostics["runtime_state"], "server_not_listening")
        self.assertFalse(result.diagnostics["tcp_listener"])
        self.assertFalse(result.diagnostics["http_ready"])

    def test_picar_probe_reports_listener_without_http_readiness(self) -> None:
        with (
            patch(
                "cli.automa_cli.vehicles.probes._get_json",
                return_value=(None, "GET status timed out"),
            ),
            patch(
                "cli.automa_cli.vehicles.probes._probe_tcp_endpoint",
                return_value={
                    "runtime_state": "http_unhealthy",
                    "tcp_listener": True,
                    "http_ready": False,
                },
            ),
        ):
            result = probe_picar(
                Candidate("picar", "http://piracer.local:8887", "default"),
                timeout_s=0.1,
            )

        self.assertFalse(result.active)
        self.assertIn("TCP listener is reachable", result.error or "")
        self.assertEqual(result.diagnostics["runtime_state"], "http_unhealthy")

    def test_retired_discovery_command_is_unavailable(self):
        result = invoke_automa("vehicles", "active", check=False)
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
