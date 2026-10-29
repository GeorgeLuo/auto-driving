from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

from tests.support.cli_runner import WORKSPACE_ROOT as ROOT


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class RequestOrderingTests(unittest.TestCase):
    def test_late_controls_and_polls_cannot_restore_stale_runs_or_cancel_selections(self):
        result = subprocess.run(
            ["node", str(Path(__file__).with_name("request_ordering.js"))],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("request ordering ok", result.stdout)
