from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

from tests.support.cli_runner import WORKSPACE_ROOT as ROOT


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class FrameStatusTests(unittest.TestCase):
    def test_status_box_is_scrollable_only_while_its_text_overflows(self) -> None:
        result = subprocess.run(
            ["node", str(Path(__file__).with_name("frame_status.js"))],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("frame status ok", result.stdout)
