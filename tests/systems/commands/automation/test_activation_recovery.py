from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.shared.cli.invoke import invoke_automa
from tests.support.runtime_fixtures import write_runtime_fixture

VEHICLE_ID = "chase-sim-chaser"


class StagedActivationRecoveryTests(unittest.TestCase):
    def test_invalid_restart_preserves_an_existing_process_and_its_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
            try:
                paths = write_runtime_fixture(runtime_root, VEHICLE_ID, pid=child.pid)
                path = paths.bundle_root / "runtime/memory/active.json"
                path.parent.mkdir(parents=True)
                path.write_text('{"schema":"automa_memory_activation_v0"}', encoding="utf-8")
                process = paths.automation_process.read_bytes()
                state = paths.automation_state.read_bytes()
                result = invoke_automa(
                    "vehicles", "automation", "restart", "--id", VEHICLE_ID,
                    "--observe-only", runtime_root=runtime_root, check=False,
                )
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn("vehicles update memory", result.stdout)
                self.assertIsNone(child.poll())
                self.assertEqual(paths.automation_process.read_bytes(), process)
                self.assertEqual(paths.automation_state.read_bytes(), state)
            finally:
                child.terminate()
                child.wait(timeout=5)

    def test_an_unbounded_control_run_is_refused_before_anything_stops(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
            try:
                paths = write_runtime_fixture(runtime_root, VEHICLE_ID, pid=child.pid)
                process = paths.automation_process.read_bytes()
                for command in ("run", "restart"):
                    with self.subTest(command=command):
                        result = invoke_automa(
                            "vehicles", "automation", command, "--id", VEHICLE_ID,
                            runtime_root=runtime_root, check=False,
                        )
                        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                        self.assertIn(
                            "A run that applies control needs --num-decisions N (N > 0); "
                            "only --observe-only runs unbounded.",
                            result.stdout,
                        )
                self.assertIsNone(child.poll())
                self.assertEqual(paths.automation_process.read_bytes(), process)
            finally:
                child.terminate()
                child.wait(timeout=5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
