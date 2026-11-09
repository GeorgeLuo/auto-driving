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
    def test_runtime_package_is_linted_while_root_runtime_artifacts_are_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            root = Path(directory)
            (root / "pyproject.toml").write_text((ROOT / "pyproject.toml").read_text())
            production = root / "autonomy/runtime"
            generated = root / "runtime/bundle"
            production.mkdir(parents=True)
            generated.mkdir(parents=True)
            (generated / "artifact.py").write_text("missing_generated_name\n")
            source = production / "host.py"
            source.write_text("def run(value):\n    return value\n")
            with patch.multiple(check, ROOT=root, RUFF_BASELINE=root / "baseline.json", PATHS=(".",)):
                self.assertEqual(check.main(["--write-ruff-baseline"]), 0)
                self.assertEqual(json.loads((root / "baseline.json").read_text())["entries"], [])
                source.write_text("def run(value):\n    return 1\n")
                self.assertEqual(check.main(["--ruff-only"]), 1)

    def test_ruff_requires_preserving_improvements_and_fails_regressions(self) -> None:
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            root = Path(directory)
            (root / "pyproject.toml").write_text((ROOT / "pyproject.toml").read_text())
            source = root / "module.py"
            source.write_text(branching_function(12))
            with patch.multiple(check, ROOT=root, RUFF_BASELINE=root / "baseline.json", PATHS=(".",)):
                self.assertEqual(check.main(["--write-ruff-baseline"]), 0)
                self.assertEqual(check.main(["--ruff-only"]), 0)
                source.write_text("\n\n" + branching_function(11))
                self.assertEqual(check.main(["--ruff-only"]), 1)
                self.assertEqual(check.main(["--prune-ruff-baseline"]), 0)
                self.assertEqual(check.main(["--ruff-only"]), 0)
                source.write_text(branching_function(12))
                self.assertEqual(check.main(["--ruff-only"]), 1)
                source.write_text(branching_function(13))
                self.assertEqual(check.main(["--ruff-only"]), 1)
                source.write_text(branching_function(12) + "\ndef broken():\n    return missing\n")
                self.assertEqual(check.main(["--ruff-only"]), 1)
                self.assertEqual(check.main(["--prune-ruff-baseline"]), 1)
                self.assertEqual(check.main(["--ruff-only"]), 1)
                baseline = (root / "baseline.json").read_bytes()
                source.write_text("def broken(\n")
                self.assertNotEqual(check.main(["--ruff-only"]), 0)
                self.assertNotEqual(check.main(["--prune-ruff-baseline"]), 0)
                self.assertEqual((root / "baseline.json").read_bytes(), baseline)

    def test_readability_rules_fail_new_code_and_allow_named_parameters_and_flat_flow(self) -> None:
        many_locals = (
            "def compute():\n"
            + "".join(f"    v{i} = {i}\n" for i in range(16))
            + "    return (" + ", ".join(f"v{i}" for i in range(16)) + ")\n"
        )
        cases = {
            "PLR0914": many_locals,
            "PLR0917": "def wide(a, b, c, d, e, f):\n    return a + b + c + d + e + f\n",
            "RET505": "def choose(x):\n    if x:\n        return 1\n    else:\n        return 2\n",
            "PLR5501": "def choose(x):\n    if x:\n        print(1)\n    else:\n        if x is None:\n            print(2)\n",
            "PIE810": "def choose(x):\n    return x.startswith('a') or x.startswith('b')\n",
            "PLR1704": "def show(value, values):\n    print(value)\n    for value in values:\n        print(value)\n",
            "SIM116": "def choose(x):\n    if x == 'a':\n        return 1\n    elif x == 'b':\n        return 2\n    elif x == 'c':\n        return 3\n    return 4\n",
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pyproject.toml").write_text((ROOT / "pyproject.toml").read_text())
            (root / "baseline.json").write_text('{"version": 1, "entries": []}')
            source = root / "module.py"
            with patch.multiple(check, ROOT=root, RUFF_BASELINE=root / "baseline.json", PATHS=(".",)):
                for rule, code in cases.items():
                    with self.subTest(rule=rule), contextlib.redirect_stdout(io.StringIO()) as output:
                        source.write_text(code)
                        self.assertEqual(check.main(["--ruff-only"]), 1)
                        self.assertIn(rule, output.getvalue())
                source.write_text(
                    "def options(a, b, *, c, d, e, f):\n    return a + b + c + d + e + f\n\n"
                    "def choose(x):\n    if x is None:\n        return 1\n    return x\n"
                )
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(check.main(["--ruff-only"]), 0)

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
