from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class SeekQueueTests(unittest.TestCase):
    def test_seeks_and_resume_queued_behind_other_requests_are_sent_after_them(self) -> None:
        result = subprocess.run(
            ["node", str(Path(__file__).with_name("seek_queue.js"))],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("seek ok", result.stdout)
