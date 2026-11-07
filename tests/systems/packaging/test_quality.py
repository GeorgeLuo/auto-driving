from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.validation import check


ROOT = Path(__file__).resolve().parents[3]


def branching_function(branches: int) -> str:
    return (
        "def choose(value: int) -> int:\n"
        + "".join(f"    if value == {i}:\n        return {i}\n" for i in range(branches))
        + "    return -1\n"
    )


class QualityCommandTests(unittest.TestCase):
    def test_ruff_passes_existing_and_improved_code_but_fails_regressions(self) -> None:
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            root = Path(directory)
            (root / "pyproject.toml").write_text((ROOT / "pyproject.toml").read_text())
            source = root / "module.py"
            source.write_text(branching_function(12))
            with patch.multiple(check, ROOT=root, RUFF_BASELINE=root / "baseline.json", PATHS=(".",)):
                self.assertEqual(check.main(["--write-ruff-baseline"]), 0)
                source.write_text("\n\n" + branching_function(11))
                self.assertEqual(check.main(["--ruff-only"]), 0)
                source.write_text(branching_function(13))
                self.assertEqual(check.main(["--ruff-only"]), 1)
                source.write_text(branching_function(12) + "\ndef broken():\n    return missing\n")
                self.assertEqual(check.main(["--ruff-only"]), 1)
                source.write_text("def broken(\n")
                self.assertNotEqual(check.main(["--ruff-only"]), 0)

    def test_type_baseline_does_not_hide_new_any_or_redundant_checks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pyrightconfig.json").write_text(json.dumps({
                "include": ["module.py"], "typeCheckingMode": "basic",
                "reportExplicitAny": "error", "reportUnnecessaryIsInstance": "error",
            }))
            source = root / "module.py"
            original = "from typing import Any\n\ndef old(value: Any) -> None:\n    pass\n"
            source.write_text(original)
            command = [sys.executable, "-m", "basedpyright", "--pythonpath", sys.executable]
            accepted = subprocess.run([*command, "--writebaseline"], cwd=root, capture_output=True, text=True)
            self.assertEqual(accepted.returncode, 1, accepted.stdout + accepted.stderr)
            self.assertTrue((root / ".basedpyright/baseline.json").is_file())
            clean = subprocess.run(command, cwd=root, capture_output=True, text=True)
            self.assertEqual(clean.returncode, 0, clean.stdout + clean.stderr)
            source.write_text(original + "\ndef new(value: Any, count: int) -> bool:\n    return isinstance(count, int)\n")
            changed = subprocess.run(command, cwd=root, capture_output=True, text=True)
            self.assertEqual(changed.returncode, 1, changed.stdout + changed.stderr)
            self.assertIn("reportExplicitAny", changed.stdout)
            self.assertIn("reportUnnecessaryIsInstance", changed.stdout)


if __name__ == "__main__":
    unittest.main()
