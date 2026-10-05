from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class PerceptionStateTests(unittest.TestCase):
    def test_absent_frame_is_distinct_from_no_output_and_completed_perception(self) -> None:
        result = subprocess.run(
            ["node", str(Path(__file__).with_name("perception_state.js"))],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("perception state ok", result.stdout)
