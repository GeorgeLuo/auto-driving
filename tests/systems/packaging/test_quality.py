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
from scripts.validation.text_evaluators import ruff_python
from qca import evaluate
from qca.text_evaluators import python_patterns


ROOT = Path(__file__).resolve().parents[3]


def branching_function(branches: int) -> str:
    return (
        "def choose(value: int) -> int:\n"
        + "".join(f"    if value == {i}:\n        return {i}\n" for i in range(branches))
        + "    return -1\n"
    )


class QualityCommandTests(unittest.TestCase):
    def test_external_engine_evaluates_text_without_a_repository(self) -> None:
        text = "def run(a, b, c):\n    if a and b and c:\n        return missing\n"
        with tempfile.TemporaryDirectory() as directory, contextlib.chdir(directory):
            results = evaluate(text, [ruff_python, python_patterns])
        result = next(item for item in results if item["type"] == "ruff.F821")
        span = result["location"]
        self.assertEqual(text[span["start"]:span["end"]], "missing")
        conditional = next(item for item in results if item["type"] == "qca.opaque_conditional")
        span = conditional["location"]
        self.assertEqual(text[span["start"]:span["end"]], "a and b and c")
        self.assertEqual(set(conditional), set(result))

    def test_default_scope_includes_deployment_source_but_excludes_generated_vendor(self) -> None:
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()) as output:
            root = Path(directory)
            (root / "pyproject.toml").write_text((ROOT / "pyproject.toml").read_text())
            (root / "baseline.json").write_text('{"version": 1, "entries": []}')
            for name in check.PATHS:
                (root / name).mkdir(parents=True, exist_ok=True)
            app = root / "deploy/targets/donkeycar/app"
            vendor = root / "deploy/targets/donkeycar/vendor/donkeycar"
            app.mkdir(parents=True)
            vendor.mkdir(parents=True)
            (app / "manage.py").write_text("def drive():\n    return 0\n")
            (vendor / "generated.py").write_text("invalid python(\n")
            # Keep the actual configured roots: overriding PATHS would hide a scope regression.
            with patch.multiple(check, ROOT=root, RUFF_BASELINE=root / "baseline.json"):
                self.assertEqual(check.main(["--ruff-only"]), 0)
                (app / "manage.py").write_text("def drive():\n    return undefined_name\n")
                self.assertEqual(check.main(["--ruff-only"]), 1)
                self.assertIn("deploy/targets/donkeycar/app/manage.py", output.getvalue())
                self.assertIn("F821", output.getvalue())

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
            "E722": "def choose(value):\n    try:\n        return int(value)\n    except:\n        return None\n",
            "B006": "def collect(values=[]):\n    values.append(1)\n    return values\n",
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

    def test_quality_artifact_records_blocking_and_baselined_findings(self) -> None:
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            root = Path(directory)
            (root / "pyproject.toml").write_text((ROOT / "pyproject.toml").read_text())
            source = root / "module.py"
            source.write_text("def old():\n    return missing\n")
            with patch.multiple(check, ROOT=root, RUFF_BASELINE=root / "baseline.json", PATHS=(".",)):
                self.assertEqual(check.main(["--write-ruff-baseline"]), 0)
                source.write_text("def old():\n    return missing\n\ndef new():\n    return other\n")
                self.assertEqual(check.main(["--ruff-only", "--output", str(root / "out")]), 1)
            artifact = json.loads((root / "out/findings.json").read_text())
            found = {item["location"]["symbol"]: item for item in artifact["findings"]}
            self.assertEqual(found["old"]["status"], "baselined")
            self.assertEqual(found["new"]["status"], "blocking")
            self.assertEqual(found["new"]["type"], "ruff.F821")
            self.assertEqual(found["new"]["location"]["line"], 5)
            self.assertTrue(found["new"]["question"])

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
            records = []
            with patch.object(check, "ROOT", root), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(check.check_types(records), 1)
            any_finding = next(item for item in records if item["type"] == "type.reportExplicitAny")
            self.assertEqual(any_finding["location"]["path"], "module.py")
            self.assertEqual(any_finding["location"]["line"], 6)
            self.assertEqual(any_finding["status"], "blocking")


if __name__ == "__main__":
    unittest.main()
