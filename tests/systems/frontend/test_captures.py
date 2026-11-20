from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

from tests.support.cli_runner import WORKSPACE_ROOT as ROOT


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class CaptureWindowTests(unittest.TestCase):
    def test_list_metadata_viewer_step_seek_and_playback(self) -> None:
        result = subprocess.run(
            ["node", str(Path(__file__).with_name("captures.js"))],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
