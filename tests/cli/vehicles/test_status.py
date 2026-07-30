from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from cli.automa_cli.vehicles import (
    Candidate,
    ProbeResult,
    format_vehicle_status,
    get_vehicle_status,
    normalize_chase_url,
)
from tests.support.cli_runner import run_automa


def _passive_vehicle() -> dict:
    return {
        "vehicle_id": "chase-sim-chaser",
        "vehicle_kind": "chase-sim-ws",
        "provider": "chase-sim",
        "connection": {
            "ws_url": "ws://localhost:5050/ws/control",
            "source": "cli",
        },
        "capabilities": {"sensors": {"front_camera": {"sensor_kind": "camera"}}},
        "status": {
            "ok": True,
            "metrics_ui": {
                "game_id": "chase",
                "scenario": "any-current-scenario",
                "chaser_control_source": "keyboard",
            },
            "passive_capture": {
                "schema": "chase_passive_capture_v1",
                "status": "available",
                "code": None,
                "sensor": {
                    "capture_id": "capture-1",
                    "simulator_frame_index": 12,
                    "simulation_epoch": "epoch-1",
                },
                "evaluator_reference": {
                    "status": "unavailable",
                    "reason": "reference_missing",
                    "path": "evaluator.reference",
                },
                "session_preservation": {
                    "preserved": True,
                    "unknown_fields": [],
                    "changed_fields": [],
                },
                "mutation_attempted": False,
            },
        },
    }


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

    def test_status_default_honors_existing_chase_environment_url(self) -> None:
        endpoint = "ws://metrics.example.test:6060/custom/control"
        probe_result = ProbeResult(
            active=True,
            candidate=Candidate("chase-sim", endpoint, "cli"),
            vehicle=_passive_vehicle(),
        )
        with (
            patch.dict(
                "os.environ",
                {"CHASE_UI_WS_URL": endpoint},
            ),
            patch(
                "cli.automa_cli.vehicles._probe_chase_sim",
                return_value=probe_result,
            ) as probe,
            patch(
                "cli.automa_cli.automation._collect_automation_status",
                return_value=[],
            ),
        ):
            payload = get_vehicle_status(vehicle_id="chase-sim-chaser")

        self.assertEqual(payload["endpoint"]["resolved_ws_url"], endpoint)
        self.assertEqual(probe.call_args.args[0].url, endpoint)

    def test_targeted_status_has_every_layer_and_one_staging_recovery(self) -> None:
        candidate = Candidate(
            "chase-sim",
            "ws://localhost:5050/ws/control",
            "cli",
        )
        probe = ProbeResult(
            active=True,
            candidate=candidate,
            vehicle=_passive_vehicle(),
            diagnostics={
                "ws_server": True,
                "frontend_connected": True,
                "chase_loaded": True,
                "front_view_ready": True,
                "passive_capture": "available",
                "elapsed_ms": 5,
                "phases": {},
            },
        )
        with patch(
            "cli.automa_cli.vehicles._probe_chase_sim",
            return_value=probe,
        ), patch(
            "cli.automa_cli.automation._collect_automation_status",
            return_value=[],
        ):
            payload = get_vehicle_status(
                vehicle_id="chase-sim-chaser",
                chase_url="http://localhost:5050",
            )

        self.assertEqual(payload["schema"], "automa_vehicle_status_v1")
        self.assertEqual(
            set(payload),
            {
                "schema",
                "vehicle_id",
                "endpoint",
                "layers",
                "capture",
                "readiness",
                "next_action",
                "checked_at_ms",
                "timeout_s",
                "elapsed_ms",
                "diagnostics",
            },
        )
        self.assertEqual(
            set(payload["layers"]),
            {
                "simulator_server",
                "simulator_frontend",
                "chase_game",
                "vehicle",
                "passive_capture",
                "automation_deployment",
                "automation_worker",
                "perception_view",
            },
        )
        self.assertEqual(payload["layers"]["vehicle"]["state"], "discoverable")
        self.assertEqual(
            payload["capture"]["evaluator_reference"]["status"],
            "unavailable",
        )
        self.assertEqual(
            payload["next_action"]["command"],
            "./cli/automa vehicles update perception "
            "--id chase-sim-chaser --algorithm lightweight_observer",
        )
        self.assertIsNone(payload["next_action"]["external_change"])

        human = format_vehicle_status(payload)
        self.assertIn("vehicle: discoverable", human)
        self.assertIn("automation_worker: stopped", human)
        self.assertEqual(human.count("Next action:"), 1)

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

    def test_running_observe_only_worker_reports_authority_and_current_view(self) -> None:
        candidate = Candidate(
            "chase-sim",
            "ws://localhost:5050/ws/control",
            "cli",
        )
        probe = ProbeResult(
            active=True,
            candidate=candidate,
            vehicle=_passive_vehicle(),
            diagnostics={
                "ws_server": True,
                "frontend_connected": True,
                "chase_loaded": True,
                "front_view_ready": True,
                "passive_capture": "available",
            },
        )
        automation = {
            "vehicle_id": "chase-sim-chaser",
            "deployed": True,
            "bundle_root": "runtime/vehicles/chase-sim-chaser/bundle",
            "process": {
                "pid": 4242,
                "running": True,
                "status": "running",
                "generation_matches": True,
            },
            "state": {
                "pid": 4242,
                "run_id": "run-current",
                "status": "running",
                "control_source": "keyboard",
                "action_policy": "observe_only",
                "control_application": "not_applied",
            },
            "published_view": {
                "available": True,
                "status": "running",
                "url": "http://127.0.0.1:8555/",
                "run_id": "run-current",
                "worker_pid": 4242,
                "has_frame": True,
                "has_perception": True,
                "latest_frame_id": "frame_1",
                "latest_perception_frame_id": "frame_1",
            },
        }
        with patch(
            "cli.automa_cli.vehicles._probe_chase_sim",
            return_value=probe,
        ), patch(
            "cli.automa_cli.automation._collect_automation_status",
            return_value=[automation],
        ):
            payload = get_vehicle_status(vehicle_id="chase-sim-chaser")

        self.assertEqual(payload["readiness"]["status"], "ready")
        self.assertIsNone(payload["next_action"])
        self.assertEqual(
            payload["layers"]["automation_worker"]["details"]["authority"][
                "action_policy"
            ],
            "observe_only",
        )
        self.assertEqual(
            payload["layers"]["perception_view"]["state"],
            "available",
        )
        human = format_vehicle_status(payload)
        self.assertIn("action_policy=observe_only", human)
        self.assertIn("control_application=not_applied", human)
        self.assertIn("Ready for: inspect perception and stop automation", human)
        self.assertNotIn("Next action:", human)

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
