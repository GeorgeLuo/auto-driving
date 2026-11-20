from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

from tests.support.cli_runner import WORKSPACE_ROOT as ROOT


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class SimulatorDeleteTests(unittest.TestCase):
    def test_simulator_delete_confirmation_and_cancellation(self) -> None:
        result = subprocess.run(
            ["node", str(Path(__file__).with_name("simulator_delete.js"))],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
