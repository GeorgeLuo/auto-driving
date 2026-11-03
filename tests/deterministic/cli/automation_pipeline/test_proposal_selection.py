from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from cli.automa_cli.hosts.runs.render import RunOutput
from cli.automa_cli.hosts.runs.service import run_host_automation
from cli.automa_cli.hosts.bundles import sync_controller_bundle
from cli.automa_cli.releases.paths import controller_bundle_paths
from cli.automa_cli.frontend.server import RuntimeViewServer
from cli.automa_cli.hosts.views.runtime_view import runtime_view_server
from cli.automa_cli.hosts.step_activations import decision_identity, stage_activation
from implementations.decision_cycle.catalog import packaged_activation
from tests.shared.cli.automation_pipeline.pipeline_fixtures import (
    HOST_NAME,
    _SlowMapper,
    _write_activations,
    chase_runtime,
    staged_runners,
)


class AutomationProposalSelectionTests(unittest.TestCase):
    def run_with_restage(self, restaged=None, *, before_run=None):
        """Run four frames on a host started from the startup stage.

        ``before_run`` is staged once the host is up and before the run starts;
        ``restaged`` is staged with the CLI's writer during the first frame.
        """

        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            bundle = controller_bundle_paths(runtime_root / HOST_NAME)
            sync_controller_bundle(bundle, output=None)
            _write_activations(bundle)
            startup = decision_identity(bundle)["generation_id"]
            applied = []
            servers = []
            calls = ExitStack()

            def record_server(**kwargs):
                servers.append(runtime_view_server(**kwargs))
                return servers[-1]

            def wrap_proposal(step_name, step):
                if step_name != "proposal":
                    return
                run = step.__call__

                def run_with_cli_restage(runner, context):
                    result = run(context)
                    applied.append(runner.plugin_ids)
                    if len(applied) == 1 and restaged is not None:
                        stage_activation(bundle, restaged, host_name=HOST_NAME)
                    return result

                calls.enter_context(patch.object(type(step), "__call__", run_with_cli_restage))

            with (
                calls,
                patch("cli.automa_cli.hosts.runs.monitor.runtime_view_server", side_effect=record_server),
                # Exercise the view projection without opening a socket.
                patch.object(RuntimeViewServer, "start", lambda server: server),
                staged_runners(perception=_SlowMapper(), wrap=wrap_proposal),
                chase_runtime(runtime_root),
            ):
                if before_run is not None:
                    stage_activation(bundle, before_run, host_name=HOST_NAME)
                result = run_host_automation(
                    host_name=HOST_NAME, num_decisions=4, take_control=False,
                    transcript=RunOutput(),
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
        self.assertEqual(state["selections"]["proposal"]["status"]["plugin_ids"], [])
        self.assertEqual(state["decision"]["generation_id"], staged["generation_id"])
        self.assertEqual(state["last_frame"]["generation_id"], staged["generation_id"])
        self.assertEqual(view["identity"]["producer_generation_id"], staged["generation_id"])

    def test_a_config_restage_keeps_the_startup_generation_until_a_restart(self):
        # A failed reset now stops the cycle for every step; see
        # test_shared_selection's one-rule test.
        startup, staged, applied, state, view = self.run_with_restage(packaged_activation(
            "proposal", config_overrides={"avoid_recent_obstruction": {"steer_magnitude": 0.5}}
        ))
        self.assertNotEqual(staged["generation_id"], startup)
        self.assertEqual(applied, [("avoid_recent_obstruction",)] * 4)
        report = state["selections"]["proposal"]["status"]["plugin_report"]
        self.assertEqual(report["applied_plugin_ids"], ["avoid_recent_obstruction"])
        self.assertEqual(state["decision"]["generation_id"], startup)
        self.assertEqual(view["identity"]["producer_generation_id"], startup)
        self.assertEqual(state["last_frame"]["generation_id"], startup)

if __name__ == "__main__":
    unittest.main()
