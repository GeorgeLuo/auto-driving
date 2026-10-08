from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cli.automa_cli.automation import run_vehicle_automation
from implementations.vehicle.access import VehicleAccess
from cli.automa_cli.bundles import controller_bundle_paths, sync_controller_bundle
from cli.automa_cli.runtime_view import RuntimeViewServer
from cli.automa_cli.step_activations import decision_identity, stage_activation
from implementations.decision_cycle.catalog import packaged_activation
from tests.integration.automation_pipeline.pipeline_fixtures import (
    _FakeCar,
    _SlowMapper,
    _write_activations,
    staged_runners,
)

VEHICLE_ID = "chase-sim-chaser"


class AutomationProposalSelectionTests(unittest.TestCase):
    def run_with_restage(self, restaged, *, reject_reset=False):
        """Run four frames, staging ``restaged`` with the CLI's writer during the first.

        The restage lands mid-frame, so the first frame's publish is refused.
        """

        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            bundle = controller_bundle_paths(runtime_root / VEHICLE_ID)
            sync_controller_bundle(bundle, output=None)
            _write_activations(bundle)
            startup = decision_identity(bundle)["generation_id"]
            vehicle = {
                "id": VEHICLE_ID, "provider": "chase-sim",
                "connection": {"ws_url": "ws://unused"},
                "status": {"passive_capture": {"status": "available", "session_preservation": {
                    "preserved": True, "unknown_fields": [], "changed_fields": [],
                }}},
            }
            applied = []
            servers = []

            def record_server(**kwargs):
                servers.append(RuntimeViewServer(**kwargs))
                return servers[-1]

            def wrap_proposal(step_name, step):
                if step_name != "proposal":
                    return
                if reject_reset:
                    def failing_reset(shared_memory=None):
                        raise RuntimeError("proposal reset unavailable")

                    step.plugins["avoid_recent_obstruction"].reset = failing_reset
                run = step.run

                def run_with_cli_restage(**kwargs):
                    result = run(**kwargs)
                    applied.append(step.plugin_ids)
                    if len(applied) == 1:
                        stage_activation(bundle, restaged, vehicle_id=VEHICLE_ID)
                    return result

                step.run = run_with_cli_restage

            with (
                patch("cli.automa_cli.automation.RUNTIME_ROOT", runtime_root),
                patch("cli.automa_cli.automation.discover_active_vehicles", return_value={}),
                patch("cli.automa_cli.automation.find_vehicle_by_id", return_value=(vehicle, None)),
                patch(
                    "cli.automa_cli.automation.create_vehicle_access",
                    lambda vehicle, *, timeout_s: VehicleAccess(
                        car=_FakeCar(),
                        image_extension="png",
                        front_camera_endpoint="atomic-evaluation-capture",
                    ),
                ),
                patch("cli.automa_cli.automation.RuntimeViewServer", side_effect=record_server),
                staged_runners(perception=_SlowMapper(), wrap=wrap_proposal),
            ):
                result = run_vehicle_automation(
                    vehicle_id=VEHICLE_ID, interval_s=0.4, num_decisions=4, take_control=False,
                )
            self.assertEqual(result.exit_code, 0, result.message)
            automation_dir = Path(bundle["runtime_dir"]) / "automation"
            state = json.loads((automation_dir / "state.json").read_text())
            latest_path = automation_dir / "latest_decision.json"
            latest = json.loads(latest_path.read_text()) if latest_path.exists() else None
            view = servers[0].decision.health_payload()
            return startup, decision_identity(bundle), applied, state, latest, view

    def test_a_restaged_plugin_list_runs_and_publishes_from_the_next_frame(self):
        startup, staged, applied, state, latest, view = self.run_with_restage(
            packaged_activation("proposal", [])
        )

        self.assertNotEqual(staged["generation_id"], startup)
        self.assertEqual(applied, [("avoid_recent_obstruction",), (), (), ()])
        self.assertEqual(state["proposal"]["status"]["plugin_ids"], [])
        self.assertEqual(state["decision"]["generation_id"], staged["generation_id"])
        # Skips also count decision view publishes; only the restaged frame's is refused.
        self.assertEqual(state["decision"]["latest_frame_publish_skips"], 1)
        self.assertEqual(latest["generation_id"], staged["generation_id"])
        self.assertEqual(view["identity"]["activation_generation_id"], staged["generation_id"])

    def test_restages_the_worker_cannot_apply_keep_the_startup_generation(self):
        cases = {
            "config change waits for a restart": (packaged_activation(
                "proposal", config_overrides={"avoid_recent_obstruction": {"steer_magnitude": 0.5}}
            ), False),
            "failed plugin reset": (packaged_activation("proposal", []), True),
        }
        for name, (restaged, reject_reset) in cases.items():
            with self.subTest(name):
                startup, staged, applied, state, latest, view = self.run_with_restage(
                    restaged, reject_reset=reject_reset,
                )
                self.assertNotEqual(staged["generation_id"], startup)
                self.assertEqual(applied, [("avoid_recent_obstruction",)] * 4)
                report = state["proposal"]["status"]["plugin_report"]
                self.assertEqual(report["applied_plugin_ids"], ["avoid_recent_obstruction"])
                self.assertEqual(state["decision"]["generation_id"], startup)
                self.assertEqual(view["identity"]["activation_generation_id"], startup)
                self.assertEqual(state["decision"]["latest_frame_publish_skips"], 4)
                self.assertIsNone(latest)

if __name__ == "__main__":
    unittest.main()
