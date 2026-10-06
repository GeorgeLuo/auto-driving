from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class RuntimeViewMemoryTests(unittest.TestCase):
    def test_memory_and_perception_pages_show_every_plugin_and_the_publisher(self) -> None:
        result = subprocess.run(
            ["node", str(Path(__file__).with_name("runtime_view_memory.js"))],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("runtime view memory ok", result.stdout)
