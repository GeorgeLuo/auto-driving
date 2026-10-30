from __future__ import annotations

import os
import tempfile
from pathlib import Path
import subprocess
import unittest

from tests.support.cli_runner import WORKSPACE_ROOT as ROOT


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

    def test_stop_disables_service_without_changing_drive_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sudo = root / "sudo"
            sudo.write_text('#!/bin/sh\nprintf "%s\\n" "$*" > "$SERVICE_CALL_LOG"\n')
            sudo.chmod(0o755)
            result = subprocess.run(
                ["bash", str(SERVICE_DIR / "control.sh"), str(root / "home"), "stop"],
                env={**os.environ, "PATH": f"{root}:{os.environ['PATH']}",
                     "SERVICE_CALL_LOG": str(root / "called")},
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((root / "called").read_text().strip(),
                             "systemctl disable --now automa-donkey.service")
            self.assertFalse((root / "home").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
