from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.shared.cli.invoke import invoke_automa
from tests.support.runtime_fixtures import write_runtime_fixture

HOST_NAME = "chase-sim-chaser"


class StagedActivationRecoveryTests(unittest.TestCase):
    def test_invalid_restart_preserves_an_existing_process_and_its_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
            try:
                paths = write_runtime_fixture(runtime_root, HOST_NAME, pid=child.pid)
                path = paths.bundle_root / "runtime/memory/active.json"
                path.parent.mkdir(parents=True)
                path.write_text('{"schema":"automa_memory_activation_v0"}', encoding="utf-8")
                process = paths.automation_process.read_bytes()
                state = paths.automation_state.read_bytes()
                result = invoke_automa(
                    "hosts", "restart", "--name", HOST_NAME,
                    "--observe-only", runtime_root=runtime_root, check=False,
                )
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn(f"hosts update --name {HOST_NAME}", result.stdout)
                self.assertIsNone(child.poll())
                self.assertEqual(paths.automation_process.read_bytes(), process)
                self.assertEqual(paths.automation_state.read_bytes(), state)
            finally:
                child.terminate()
                child.wait(timeout=5)

    def test_a_control_run_without_a_bound_or_opt_in_is_refused_before_anything_stops(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
            try:
                paths = write_runtime_fixture(runtime_root, HOST_NAME, pid=child.pid)
                process = paths.automation_process.read_bytes()
                for command in ("run", "restart"):
                    with self.subTest(command=command):
                        result = invoke_automa(
                            "hosts", command, "--name", HOST_NAME,
                            runtime_root=runtime_root, check=False,
                        )
                        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                        self.assertIn(
                            "A run that applies control needs --num-decisions N (N > 0); "
                            "use --unbounded to run until stopped.",
                            result.stdout,
                        )
                self.assertIsNone(child.poll())
                self.assertEqual(paths.automation_process.read_bytes(), process)
            finally:
                child.terminate()
                child.wait(timeout=5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
