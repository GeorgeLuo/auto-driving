from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from qca.evaluation import validate_finding
from scripts.validation import check, qca_summary
from scripts.validation.findings import qca_records


class QcaQualitySummaryTests(unittest.TestCase):
    def test_quality_output_scopes_test_findings_to_committed_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def git(*args: str) -> str:
                return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()

            git("init", "-q")
            git("config", "user.name", "Quality Test")
            git("config", "user.email", "quality@example.test")
            (root / "tests").mkdir()
            source = root / "tests" / "test_example.py"
            before = (
                "def test_example(self, value, output):\n"
                "    self.assertEqual(value, 'unchanged')\n"
                "    self.assertIn(\n"
                "        'old', output\n"
                "    )\n"
                "    assigned = 2\n"
                "    assert assigned == 1\n"
            )
            source.write_text(before)
            git("add", ".")
            git("commit", "-qm", "base")
            base = git("rev-parse", "HEAD")
            source.write_text("from pkg.impl import _private\n" + before.replace("'old'", "'new'").replace("assigned = 2", "assigned = 1"))
            git("add", ".")
            git("commit", "-qm", "head")
            head = git("rev-parse", "HEAD")
            source.write_text(before)  # The inspected revisions must win over uncommitted edits.

            output = root / "evidence"
            records = []
            repo = Path(__file__).resolve().parents[3]
            pythonpath = os.pathsep.join(filter(None, (str(repo), os.environ.get("PYTHONPATH"))))
            with patch.dict(os.environ, {"PYTHONPATH": pythonpath}):
                errors = check.inspect_changes(output, records, root=root, base=base, head=head)
            self.assertEqual(errors, [])
            test_records = [item for item in records if item["type"] in {
                "qca.private_import", "qca.string_expected_assertion", "qca.assignment_readback"
            }]
            self.assertEqual(len(test_records), 3)
            self.assertEqual({item["type"] for item in test_records}, {
                "qca.private_import", "qca.string_expected_assertion", "qca.assignment_readback"
            })
            for item in test_records:
                validate_finding(item)
                self.assertEqual(item["status"], "review")
                self.assertEqual(item["location"]["path"], "tests/test_example.py")
            membership = next(item for item in test_records if item["type"] == "qca.string_expected_assertion")
            self.assertEqual(membership["location"]["line"], 4)
            self.assertEqual(membership["evidence"]["comparison_kinds"], ["membership"])
            self.assertIn("member or substring", membership["question"])
            readback = next(item for item in test_records if item["type"] == "qca.assignment_readback")
            self.assertEqual(readback["location"]["line"], 8)
            self.assertEqual(readback["related"], [{"path": "tests/test_example.py", "line": 7}])

    def test_committed_change_produces_summary_and_complete_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def git(*args: str) -> str:
                return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()

            git("init", "-q")
            git("config", "user.name", "Quality Test")
            git("config", "user.email", "quality@example.test")
            (root / "autonomy").mkdir()
            source = root / "autonomy" / "module.py"
            source.write_text("def choose(a, b, c):\n    return a\n")
            git("add", ".")
            git("commit", "-qm", "base")
            base = git("rev-parse", "HEAD")
            source.write_text("def choose(a, b, c):\n    if a and b and c:\n        return b\n    return a\n")
            git("add", ".")
            git("commit", "-qm", "head")
            head = git("rev-parse", "HEAD")
            output = root / "evidence"
            with contextlib.chdir(root), patch.object(qca_summary, "PATHS", (".",)), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(qca_summary.main(["--base", base, "--head", head, "--output", str(output)]), 0)
            report = json.loads((output / "report.json").read_text())
            self.assertEqual(report["identity"]["base_sha"], base)
            self.assertEqual(report["identity"]["head_sha"], head)
            self.assertEqual(report["factors"]["patterns"]["metrics"]["opaque_conditional_count"], 1)
            self.assertIn("autonomy/module.py:2", (output / "summary.md").read_text())
            self.assertIn("<!doctype html>", (output / "report.html").read_text().lower())
            inspection = json.loads((output / "inspection.json").read_text())
            self.assertEqual((inspection["base_sha"], inspection["head_sha"]), (base, head))
            self.assertIn("Production usage and related implementations", (output / "summary.md").read_text())
            findings = qca_records(report, inspection, changed_lines={})
            conditional = next(item for item in findings if item["type"] == "qca.opaque_conditional")
            self.assertEqual(conditional["location"], {"path": "autonomy/module.py", "line": 2})
            self.assertTrue(conditional["question"])
            self.assertEqual(conditional["status"], "review")


if __name__ == "__main__":
    unittest.main()
