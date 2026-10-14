from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class PluginCatalogStateTests(unittest.TestCase):
    def test_all_steps_follow_applied_selection_and_polling_recovery(self):
        result = subprocess.run(
            ["node", str(Path(__file__).with_name("plugin_catalog_state.js"))],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("plugin catalog state ok", result.stdout)
