from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class PluginSelectionQueueTests(unittest.TestCase):
    def test_toggles_during_a_request_send_only_the_latest_selection_after_it(self) -> None:
        result = subprocess.run(
            ["node", str(Path(__file__).with_name("plugin_queue.js"))],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("queue ok", result.stdout)
