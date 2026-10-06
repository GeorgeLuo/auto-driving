from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from cli.automa_cli.workbench_runner import ImageReplayRunner
from implementations.decision_cycle.catalog import selection_activation

ROOT = Path(__file__).resolve().parents[3]


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class PluginSelectionTests(unittest.TestCase):
    def _check_page(self, mode: str) -> None:
        # Use the server's actual catalog, keys, presets and execution order.
        state = ImageReplayRunner(
            activations={"perception": selection_activation("perception", preset="multi_obstruction")}
        ).state()
        result = subprocess.run(
            ["node", str(Path(__file__).with_name("plugin_selection.js")), mode],
            cwd=ROOT,
            input=json.dumps(state),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"plugin selection {mode} ok", result.stdout)

    def test_toggling_preserves_the_cli_selection_order(self) -> None:
        self._check_page("order")

    def test_polled_selections_update_controls_without_losing_local_drafts(self) -> None:
        self._check_page("state")

    def test_each_panel_shows_execution_order_and_its_preset_when_present(self) -> None:
        self._check_page("visibility")
