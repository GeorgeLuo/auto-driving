from __future__ import annotations

import unittest
from unittest.mock import patch

from cli.automa_cli.providers.urls import normalize_chase_url
from cli.automa_cli.providers.models import Candidate
from cli.automa_cli.vehicles.render import format_vehicles_status
from tests.shared.cli.invoke import invoke_automa


class VehicleStatusTests(unittest.TestCase):
    def test_normalizes_http_origins_and_preserves_explicit_ws_paths(self) -> None:
        self.assertEqual(
            normalize_chase_url("http://localhost:5050"),
            "ws://localhost:5050/ws/control",
        )
        self.assertEqual(
            normalize_chase_url("https://metrics.example.test/play"),
            "wss://metrics.example.test/ws/control",
        )
        self.assertEqual(
            normalize_chase_url("ws://localhost:5050"),
            "ws://localhost:5050/ws/control",
        )
        self.assertEqual(
            normalize_chase_url("wss://metrics.example.test/custom?token=x"),
            "wss://metrics.example.test/custom?token=x",
        )
        with self.assertRaisesRegex(ValueError, "scheme"):
            normalize_chase_url("ftp://localhost:5050")

    def test_mid_capture_frontend_delivery_failure_recovers_with_browser_reload(
        self,
    ) -> None:
        """frontend_unresponsive during sensor_capture must not become a capability hint."""

        from implementations.vehicle.chase_sim import ChasePassiveCaptureError

        candidate = Candidate(
            "chase-sim",
            "ws://localhost:5050/ws/control",
            "cli",
        )

        def _raise_frontend_unresponsive(*_args, **_kwargs):
            raise ChasePassiveCaptureError(
                code="frontend_disconnected",
                message="Passive capture could not complete sensor_capture",
                details={
                    "incomplete_phase": "sensor_capture",
                    "protocol_code": "frontend_unresponsive",
                    "protocol_error": {
                        "code": "frontend_unresponsive",
                        "command": "play_game_query",
                    },
                    "phases": {
                        "state_before": {"status": "complete", "duration_ms": 1},
                        "debug_before": {"status": "complete", "duration_ms": 1},
                    },
                },
            )

        with patch(
            "cli.automa_cli.providers.probes.ChaseSimCar.inspect_passive_capture",
            side_effect=_raise_frontend_unresponsive,
        ):
            from cli.automa_cli.providers.probes import probe_chase

            probe = probe_chase(candidate, timeout_s=1.0)

        self.assertFalse(probe.active)
        self.assertFalse(probe.diagnostics["frontend_connected"])
        self.assertEqual(probe.diagnostics["error_code"], "frontend_disconnected")
        self.assertIn("Open or reload", probe.error or "")
        self.assertNotIn("atomic-evaluation-capture", probe.error or "")

    def test_status_renderer_formats_supplied_vehicles(self) -> None:
        text = format_vehicles_status({"vehicles": [
            {
                "provider": "chase-sim",
                "state": "running",
                "url": "ws://localhost:5050/ws/control",
                "frontend": "connected",
                "game": "chase",
                "pid": 42,
                "error": None,
            },
            {
                "provider": "picar",
                "state": "unreachable",
                "url": "http://pi.local",
                "error": "timed out",
            },
        ]})

        self.assertEqual(text, (
            "Simulator (chase-sim)  running at ws://localhost:5050/ws/control\n"
            "  frontend connected, game chase\n"
            "  created by simulators create, pid 42\n"
            "\n"
            "PiCar (picar)  unreachable at http://pi.local: timed out"
        ))

    def test_retired_discovery_command_is_unavailable(self):
        result = invoke_automa("vehicles", "active", check=False)
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
