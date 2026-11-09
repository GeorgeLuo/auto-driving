from __future__ import annotations

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.validation import check
from qca.evaluation import finding


ROOT = Path(__file__).resolve().parents[3]


class QualityCommandTests(unittest.TestCase):
    def test_failed_evaluator_preserves_prior_findings_and_records_failure(self) -> None:
        def first(_context):
            return [finding("example", {"path": "module.py", "line": 1}, "Question?", "test", "rule", evidence={})]
        def fail(_context):
            raise ValueError("tool failed")
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stderr(io.StringIO()):
            output = Path(directory)
            with patch.object(check, "changed_lines", return_value={}), \
                 patch.object(check, "evaluate_ruff", first), patch.object(check, "evaluate_imports", fail):
                self.assertEqual(check.main(["--output", str(output)]), 2)
            artifact = json.loads((output / "findings.json").read_text())
            self.assertEqual([item["type"] for item in artifact["findings"]], ["example"])
            self.assertEqual(artifact["errors"], ["tool failed"])

    def test_changed_lines_uses_git_diff_and_includes_untracked_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def git(*args):
                return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()
            git("init", "-q")
            git("config", "user.name", "Quality Test")
            git("config", "user.email", "quality@example.test")
            source = root / "module.py"
            source.write_text("first\nsecond\nthird\n")
            git("add", "module.py")
            git("commit", "-qm", "base")
            source.write_text("first\nchanged\nthird\n")
            (root / "new.py").write_text("new\n")
            with patch.object(check, "ROOT", root), patch.dict("os.environ", {"PR_BASE": "HEAD", "PR_HEAD": "HEAD"}):
                changes = check.changed_lines()
            self.assertEqual(changes["module.py"], {2})
            self.assertIsNone(changes["new.py"])

    def test_default_scope_includes_deployment_source_but_excludes_generated_vendor(self) -> None:
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()) as output:
            root = Path(directory)
            (root / "pyproject.toml").write_text((ROOT / "pyproject.toml").read_text())
            for name in check.PATHS:
                (root / name).mkdir(parents=True, exist_ok=True)
            app = root / "deploy/targets/donkeycar/app"
            vendor = root / "deploy/targets/donkeycar/vendor/donkeycar"
            app.mkdir(parents=True)
            vendor.mkdir(parents=True)
            (app / "manage.py").write_text("def drive():\n    return 0\n")
            (vendor / "generated.py").write_text("invalid python(\n")
            # Keep the actual configured roots: overriding PATHS would hide a scope regression.
            with patch.multiple(check, ROOT=root), patch.object(check, "changed_lines", return_value={"deploy/targets/donkeycar/app/manage.py": None}):
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
            with patch.multiple(check, ROOT=root, PATHS=(".",)), patch.object(check, "changed_lines", return_value={"module.py": None, "autonomy/runtime/host.py": None}):
                self.assertEqual(check.main(["--ruff-only"]), 0)
                source.write_text("def run(value):\n    return 1\n")
                self.assertEqual(check.main(["--ruff-only"]), 1)

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
            source = root / "module.py"
            with patch.multiple(check, ROOT=root, PATHS=(".",)), patch.object(check, "changed_lines", return_value={"module.py": None, "autonomy/runtime/host.py": None}):
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

    def test_artifact_includes_only_findings_on_changed_lines(self) -> None:
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            root = Path(directory)
            (root / "pyproject.toml").write_text((ROOT / "pyproject.toml").read_text())
            (root / "module.py").write_text("def old():\n    return missing\n\ndef new():\n    return other\n")
            with patch.multiple(check, ROOT=root, PATHS=(".",)), patch.object(check, "changed_lines", return_value={"module.py": {5}}):
                self.assertEqual(check.main(["--ruff-only", "--output", str(root / "out")]), 1)
            artifact = json.loads((root / "out/findings.json").read_text())
            self.assertEqual(len(artifact["findings"]), 1)
            item = artifact["findings"][0]
            self.assertEqual((item["type"], item["location"]["line"]), ("ruff.F821", 5))
            self.assertEqual(item["status"], "blocking")

    def test_syntax_error_at_eof_is_reported_for_changed_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            root = Path(directory)
            (root / "pyproject.toml").write_text((ROOT / "pyproject.toml").read_text())
            (root / "module.py").write_text("def broken(\n")
            records = []
            with patch.multiple(check, ROOT=root, PATHS=(".",)):
                self.assertEqual(check.check_ruff({"module.py": {1}}, records), 1)
            self.assertEqual(records[0]["type"], "ruff.invalid-syntax")

    def test_type_findings_are_filtered_to_changed_lines(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pyrightconfig.json").write_text(json.dumps({
                "include": ["module.py"], "typeCheckingMode": "basic", "reportExplicitAny": "error",
            }))
            (root / "module.py").write_text(
                "from typing import Any\n\ndef old(value: Any) -> None:\n    pass\n\ndef new(value: Any) -> None:\n    pass\n"
            )
            records = []
            with patch.object(check, "ROOT", root), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(check.check_types({"module.py": {6}}, records), 1)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["location"], {"path": "module.py", "line": 6})
            self.assertEqual(records[0]["type"], "type.reportExplicitAny")
            with contextlib.redirect_stdout(io.StringIO()):
                via_dispatch = check.evaluate_types(check.QualityContext({"module.py": {6}}, None, root=root))
            self.assertEqual(via_dispatch, records)


if __name__ == "__main__":
    unittest.main()
