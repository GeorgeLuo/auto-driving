from __future__ import annotations

import json
import socket
import unittest
from unittest.mock import patch

from cli.automa_cli.vehicles import (
    Candidate,
    ProbeResult,
    chase_operator_url,
    format_chase_simulator_status,
    get_chase_simulator_status,
    get_vehicle_status,
    normalize_chase_url,
)
from implementations.vehicle.chase_sim.metrics_ws import MetricsUiWsClient
from implementations.vehicle.chase_sim.presence import MetricsUiPresence
from tests.shared.cli.invoke import invoke_automa
from tests.shared.implementations.runtime.wait import wait_until
from tests.support.fake_metrics_ui import fake_metrics_ui


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
                "cli.automa_cli.vehicles._probe_chase",
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
            "cli.automa_cli.vehicles._probe_chase",
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
            "./cli/automa vehicles update autonomy --id chase-sim-chaser",
        )
        self.assertIsNone(payload["next_action"]["external_change"])

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
            "cli.automa_cli.vehicles._probe_chase",
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

    def test_aggregate_separates_non_chase_local_deployments(self) -> None:
        candidate = Candidate(
            "chase-sim",
            "ws://localhost:5050/ws/control",
            "cli",
        )
        probe = ProbeResult(
            active=True,
            candidate=candidate,
            vehicle=_passive_vehicle(),
        )
        deployments = [
            {
                "vehicle_id": "chase-sim-chaser",
                "deployed": True,
                "bundle_root": "runtime/vehicles/chase-sim-chaser/bundle",
            },
            {
                "vehicle_id": "piracer",
                "deployed": True,
                "bundle_root": "runtime/vehicles/piracer/bundle",
            },
        ]
        with patch(
            "cli.automa_cli.vehicles._probe_chase",
            return_value=probe,
        ), patch(
            "cli.automa_cli.automation._collect_automation_status",
            return_value=deployments,
        ):
            payload = get_vehicle_status()

        self.assertEqual(
            [card["vehicle_id"] for card in payload["vehicles"]],
            ["chase-sim-chaser"],
        )
        self.assertEqual(
            payload["other_local_deployments"],
            [
                {
                    "vehicle_id": "piracer",
                    "inspection_command": (
                        "./cli/automa vehicles automation status --id piracer"
                    ),
                }
            ],
        )

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
            "cli.automa_cli.vehicles.ChaseSimCar.inspect_passive_capture",
            side_effect=_raise_frontend_unresponsive,
        ):
            from cli.automa_cli.vehicles import _probe_chase

            probe = _probe_chase(candidate, timeout_s=1.0)

        self.assertFalse(probe.active)
        self.assertFalse(probe.diagnostics["frontend_connected"])
        self.assertEqual(probe.diagnostics["error_code"], "frontend_disconnected")
        self.assertIn("Open or reload", probe.error or "")
        self.assertNotIn("atomic-evaluation-capture", probe.error or "")

        with patch(
            "cli.automa_cli.vehicles._probe_chase",
            return_value=probe,
        ), patch(
            "cli.automa_cli.automation._collect_automation_status",
            return_value=[],
        ):
            payload = get_vehicle_status(
                vehicle_id="chase-sim-chaser",
                chase_url="http://localhost:5050",
            )

        self.assertEqual(payload["layers"]["simulator_frontend"]["state"], "disconnected")
        self.assertEqual(payload["readiness"]["blocking_layer"], "simulator_frontend")
        self.assertIn("Open or reload", payload["next_action"]["external_change"]["change"])
        self.assertNotIn(
            "atomic-evaluation-capture",
            json.dumps(payload["next_action"]),
        )

    def test_status_shows_only_the_simulator_and_the_host_attached_to_it(self) -> None:
        host = {"kind": "vehicle-host", "vehicle_id": "chase-sim-chaser",
                "url": "http://127.0.0.1:8123", "pid": 4321}
        with fake_metrics_ui() as server, MetricsUiPresence(server.url, host, retry_s=0.05):
            wait_until(MetricsUiWsClient(server.url).list_agents, message="the host never attached")
            human = invoke_automa("vehicles", "status", "--chase-ws-url", server.url)
            machine = json.loads(invoke_automa("vehicles", "status", "--chase-ws-url", server.url, "--json").stdout)
            other = get_chase_simulator_status(vehicle_id="chase-sim-follower", chase_ws_url=server.url)

        lines = human.stdout.splitlines()
        self.assertEqual(len(lines), 2, human.stdout)
        self.assertEqual(lines[0], f"Simulator: running at {chase_operator_url(server.url)}")
        self.assertTrue(
            lines[1].startswith(
                "Host: chase-sim-chaser at http://127.0.0.1:8123 (pid 4321), attached since "
            ),
            lines[1],
        )
        self.assertEqual(
            set(machine), {"schema", "vehicle_id", "operator_url", "state", "hosts", "hosts_error"}
        )
        self.assertEqual(machine["schema"], "automa_simulator_status_v1")
        self.assertEqual(machine["state"], "running")
        self.assertEqual([item["url"] for item in machine["hosts"]], [host["url"]])
        self.assertEqual(other["hosts"], [])

    def test_status_says_when_no_host_is_attached(self) -> None:
        with fake_metrics_ui() as server:
            result = invoke_automa("vehicles", "status", "--chase-ws-url", server.url)

        self.assertEqual(
            result.stdout.splitlines(),
            [f"Simulator: running at {chase_operator_url(server.url)}", "Host: none attached"],
        )

    def test_status_exits_one_when_the_simulator_is_not_reachable(self) -> None:
        with socket.socket() as closed:
            closed.bind(("127.0.0.1", 0))
            url = f"http://127.0.0.1:{closed.getsockname()[1]}"
        result = invoke_automa("vehicles", "status", "--chase-url", url, check=False)

        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(
            result.stdout.splitlines(),
            [f"Simulator: not reachable at {chase_operator_url(normalize_chase_url(url))}",
             "Host: unknown while the simulator is unreachable"],
        )

    def test_status_says_why_hosts_are_unknown(self) -> None:
        url = "http://localhost:5050/"
        unlisted = {"state": "running", "operator_url": url, "hosts": None,
                    "hosts_error": "Frontend not connected"}
        self.assertEqual(
            format_chase_simulator_status(unlisted).splitlines(),
            [f"Simulator: running at {url}", "Host: unknown, this simulator cannot list attached hosts"],
        )

    def test_conflicting_explicit_urls_exit_two_before_any_probe(self) -> None:
        result = invoke_automa(
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
        result = invoke_automa(
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
        human = invoke_automa(
            "vehicles",
            "active",
            "--no-picar",
            "--no-sim",
        )
        self.assertIn("Discoverable vehicles: 0", human.stdout)
        self.assertNotIn("Active vehicles:", human.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
