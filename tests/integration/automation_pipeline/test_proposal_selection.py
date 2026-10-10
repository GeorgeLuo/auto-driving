from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cli.automa_cli.automation import run_vehicle_automation
from cli.automa_cli.bundles import controller_bundle_paths, sync_controller_bundle
from cli.automa_cli.runtime_view import RuntimeViewServer
from cli.automa_cli.step_activations import decision_identity, stage_activation
from implementations.decision_cycle.catalog import packaged_activation
from tests.integration.automation_pipeline.pipeline_fixtures import (
    VEHICLE_ID,
    _SlowMapper,
    _write_activations,
    chase_runtime,
    staged_runners,
)


class AutomationProposalSelectionTests(unittest.TestCase):
    def run_with_restage(self, restaged=None, *, reject_reset=False, before_run=None):
        """Run four frames on a host started from the startup stage.

        ``before_run`` is staged once the host is up and before the run starts;
        ``restaged`` is staged with the CLI's writer during the first frame.
        """

        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            bundle = controller_bundle_paths(runtime_root / VEHICLE_ID)
            sync_controller_bundle(bundle, output=None)
            _write_activations(bundle)
            startup = decision_identity(bundle)["generation_id"]
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
                    if len(applied) == 1 and restaged is not None:
                        stage_activation(bundle, restaged, vehicle_id=VEHICLE_ID)
                    return result

                step.run = run_with_cli_restage

            with (
                patch("cli.automa_cli.runtime_monitor.RuntimeViewServer", side_effect=record_server),
                # Exercise the view projection without opening a socket.
                patch.object(RuntimeViewServer, "start", lambda server: server),
                staged_runners(perception=_SlowMapper(), wrap=wrap_proposal),
                chase_runtime(runtime_root),
            ):
                if before_run is not None:
                    stage_activation(bundle, before_run, vehicle_id=VEHICLE_ID)
                result = run_vehicle_automation(
                    vehicle_id=VEHICLE_ID, interval_s=0.4, num_decisions=4, take_control=False,
                )
            self.assertEqual(result.exit_code, 0, result.message)
            state = json.loads((Path(bundle["runtime_dir"]) / "automation" / "state.json").read_text())
            view = servers[0].decision.health_payload()
            return startup, decision_identity(bundle), applied, state, view

    def test_the_run_reports_the_decision_the_host_applied(self):
        cases = {
            "selection applies live": (packaged_activation("proposal", []), True),
            "config change waits for a restart": (packaged_activation(
                "proposal", config_overrides={"avoid_recent_obstruction": {"steer_magnitude": 0.5}},
            ), False),
        }
        for name, (replacement, applies) in cases.items():
            with self.subTest(name):
                startup, staged, applied, state, view = self.run_with_restage(before_run=replacement)
                self.assertNotEqual(staged["generation_id"], startup)
                expected = staged["generation_id"] if applies else startup
                plugins = replacement.plugins if applies else ("avoid_recent_obstruction",)
                self.assertEqual(applied, [plugins] * 4)
                self.assertEqual(state["decision"]["generation_id"], expected)
                self.assertEqual(state["last_frame"]["generation_id"], expected)
                self.assertEqual(view["identity"]["producer_generation_id"], expected)

    def test_a_restaged_plugin_list_runs_and_publishes_from_the_next_frame(self):
        startup, staged, applied, state, view = self.run_with_restage(
            packaged_activation("proposal", [])
        )

        self.assertNotEqual(staged["generation_id"], startup)
        self.assertEqual(applied, [("avoid_recent_obstruction",), (), (), ()])
        self.assertEqual(state["proposal"]["status"]["plugin_ids"], [])
        self.assertEqual(state["decision"]["generation_id"], staged["generation_id"])
        self.assertEqual(state["last_frame"]["generation_id"], staged["generation_id"])
        self.assertEqual(view["identity"]["producer_generation_id"], staged["generation_id"])

    def test_restages_the_host_cannot_apply_keep_the_startup_generation(self):
        cases = {
            "config change waits for a restart": (packaged_activation(
                "proposal", config_overrides={"avoid_recent_obstruction": {"steer_magnitude": 0.5}}
            ), False),
            "failed plugin reset": (packaged_activation("proposal", []), True),
        }
        for name, (restaged, reject_reset) in cases.items():
            with self.subTest(name):
                startup, staged, applied, state, view = self.run_with_restage(
                    restaged, reject_reset=reject_reset,
                )
                self.assertNotEqual(staged["generation_id"], startup)
                self.assertEqual(applied, [("avoid_recent_obstruction",)] * 4)
                report = state["proposal"]["status"]["plugin_report"]
                self.assertEqual(report["applied_plugin_ids"], ["avoid_recent_obstruction"])
                self.assertEqual(state["decision"]["generation_id"], startup)
                self.assertEqual(view["identity"]["producer_generation_id"], startup)
                self.assertEqual(state["last_frame"]["generation_id"], startup)

if __name__ == "__main__":
    unittest.main()
