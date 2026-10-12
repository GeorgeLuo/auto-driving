from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
SERVICE_DIR = ROOT / "deploy" / "targets" / "donkeycar" / "systemd"
LAUNCHER = ROOT / "deploy" / "targets" / "donkeycar" / "app" / "automa_drive.sh"


class DonkeyRuntimeServiceTests(unittest.TestCase):
    def test_shell_assets_are_valid_and_legacy_launcher_is_removed(self) -> None:
        scripts = [SERVICE_DIR / "install.sh", SERVICE_DIR / "control.sh", LAUNCHER]
        result = subprocess.run(
            ["bash", "-n", *map(str, scripts)],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((ROOT / "scripts" / "deploy" / "donkeycar" / "restart_drive.sh").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
