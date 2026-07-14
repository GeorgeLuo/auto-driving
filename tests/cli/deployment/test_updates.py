from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from implementations.perception.catalog import DEFAULT_PERCEPTION_ALGORITHM
from tests.support.cli_runner import run_automa


class DeploymentUpdateTests(unittest.TestCase):
    def test_core_update_dry_run_can_skip_live_discovery(self) -> None:
        result = run_automa(
            "vehicles",
            "update",
            "core",
            "--id",
            "piracer",
            "--skip-discovery",
            "--ssh-target",
            "piracer@example.local",
            "--dry-run",
            "--restart",
            "--drive-args=--js",
        )

        self.assertIn("Core update dry run for piracer -> piracer@example.local", result.stdout)
        self.assertIn("would ensure DonkeyCar vendor source:", result.stdout)
        self.assertIn("deploy/targets/donkeycar/vendor/donkeycar", result.stdout)
        self.assertIn("deploy/targets/donkeycar/app", result.stdout)
        self.assertIn("--exclude=autonomy", result.stdout)
        self.assertIn("--exclude=implementations", result.stdout)
        self.assertIn("--exclude=runtime", result.stdout)
        self.assertIn("DRIVE_ARGS=--js scripts/deploy/donkeycar/restart_drive.sh", result.stdout)

    def test_autonomy_update_dry_run_is_versioned_and_machine_readable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            result = run_automa(
                "vehicles",
                "update",
                "autonomy",
                "--id",
                "piracer",
                "--skip-discovery",
                "--ssh-target",
                "piracer@example.local",
                "--dry-run",
                "--json",
                runtime_root=runtime_root,
            )

            payload = json.loads(result.stdout)
            self.assertEqual(payload["schema"], "vehicle_autonomy_update_v0")
            self.assertTrue(payload["dry_run"])
            self.assertEqual(payload["target"]["provider"], "picar")
            self.assertEqual(payload["activation"]["perception_algorithm"], DEFAULT_PERCEPTION_ALGORITHM)
            self.assertEqual(payload["activation"]["decision_engine"], "idle")
            self.assertTrue(payload["source"]["tree_sha256"])
            self.assertIn("controller-releases", payload["commands"][0]["command"])
            self.assertFalse(runtime_root.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
