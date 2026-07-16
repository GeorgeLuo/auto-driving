from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

from cli.automa_cli.deploy import (
    DONKEY_SERVICE_NAME,
    _donkey_service_control_command,
    _drive_args_token,
)


ROOT = Path(__file__).resolve().parents[3]
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

    def test_unit_supervises_boot_runtime_without_project_log_files(self) -> None:
        unit = (SERVICE_DIR / f"{DONKEY_SERVICE_NAME}.in").read_text(encoding="utf-8")

        self.assertIn("After=network-online.target", unit)
        self.assertIn("ExecStart=@AUTOMA_PI_HOME@/mycar/automa_drive.sh", unit)
        self.assertIn("Restart=always", unit)
        self.assertIn("WantedBy=multi-user.target", unit)
        self.assertIn("StandardOutput=journal", unit)
        self.assertNotIn("donkey_web.log", unit)
        self.assertNotIn("nohup", unit)

        installer = (SERVICE_DIR / "install.sh").read_text(encoding="utf-8")
        self.assertIn("rm -f", installer)
        self.assertIn("donkey_web.pid", installer)
        self.assertIn("donkey_web.log", installer)

    def test_service_control_persists_encoded_drive_arguments(self) -> None:
        self.assertEqual(_drive_args_token(None), "-")
        self.assertEqual(_drive_args_token(""), "b64:")
        self.assertEqual(_drive_args_token("--js"), "b64:LS1qcw==")

        command = _donkey_service_control_command(
            pi_home="/home/piracer",
            action="restart",
            drive_args="--js",
        )
        self.assertIn("/home/piracer/.config/automa/systemd/control.sh", command)
        self.assertIn("restart", command)
        self.assertIn("b64:LS1qcw==", command)


if __name__ == "__main__":
    unittest.main(verbosity=2)
