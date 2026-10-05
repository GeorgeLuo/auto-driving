from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class MemorySelectionStateTests(unittest.TestCase):
    def test_disabled_memory_is_distinct_from_no_frame_and_failure(self) -> None:
        result = subprocess.run(
            ["node", str(Path(__file__).with_name("memory_selection_state.js"))],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("memory selection state ok", result.stdout)
