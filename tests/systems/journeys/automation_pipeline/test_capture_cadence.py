from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cli.automa_cli.runtime_hosts import stop_chase_host
from tests.shared.cli.invoke import invoke_automa
from tests.support.cli_runner import run_automa
from tests.support.fake_metrics_ui import fake_metrics_ui_server


class ChaseCaptureCadenceTests(unittest.TestCase):


    def test_cli_num_decisions_limits_the_completed_shared_cycles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, fake_metrics_ui_server() as ws_url:
            root = Path(tmp)
            env = {"CHASE_UI_WS_URL": ws_url}
            # Staging builds the controller release the car's runtime host runs.
            invoke_automa(
                "vehicles", "update", "perception", "--id", "chase-sim-chaser",
                "--plugin", "frame", runtime_root=root, extra_env=env,
            )
            try:
                run = run_automa(
                    "vehicles", "automation", "run", "--id", "chase-sim-chaser",
                    "--observe-only", "--num-decisions", "3", "--interval-s", "0",
                    "--foreground", runtime_root=root, extra_env=env,
                )
            finally:
                with patch("cli.automa_cli.runtime_hosts.RUNTIME_ROOT", root):
                    stop_chase_host("chase-sim-chaser")
            state = json.loads((root / "chase-sim-chaser/bundle/runtime/automation/state.json").read_text())
            self.assertEqual(state["status"], "completed")
            self.assertEqual(state["num_decisions"], 3)
            self.assertEqual(state["processed_count"], 3)
            self.assertEqual(state["session"]["configuration"]["num_decisions"], 3)
            self.assertEqual(state["session"]["processed_decisions"], 3)
            self.assertIn("Decisions: 3", run.stdout)
            self.assertIn("Decisions completed: 3", run.stdout)
