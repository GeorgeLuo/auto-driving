from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


class SourceVisibilityTests(unittest.TestCase):
    def test_generated_runtime_ignore_does_not_hide_runtime_source_packages(self) -> None:
        for source_path in (
            "autonomy/runtime/engine.py",
            "implementations/runtime/donkeycar/donkey_part.py",
        ):
            result = subprocess.run(
                ["git", "check-ignore", "-q", source_path],
                cwd=ROOT,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0, source_path)

        generated = subprocess.run(
            ["git", "check-ignore", "-q", "runtime/vehicles/example/state.json"],
            cwd=ROOT,
            check=False,
        )
        self.assertEqual(generated.returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
