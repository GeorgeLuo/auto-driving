from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from qca import analyze_diff


class FactorReportTests(unittest.TestCase):
    def test_opaque_conditional_diff_has_scope_wide_delta_and_changed_file_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def git(*args):
                return subprocess.check_output(["git", *args], cwd=root, text=True).strip()

            git("init", "-q")
            git("config", "user.name", "QCA fixture")
            git("config", "user.email", "qca@example.invalid")
            app = root / "app.py"
            app.write_text("def run(a, b, c):\n    return a and b\n")
            (root / "unchanged.py").write_text("if a and b and c:\n    pass\n")
            git("add", ".")
            git("commit", "-qm", "base")
            base = git("rev-parse", "HEAD")
            app.write_text("def run(a, b, c):\n    return a and b and c\n")
            git("add", ".")
            git("commit", "-qm", "head")

            report = analyze_diff(base, "HEAD", path=root)
            factor = report.factors["patterns"]
            metric = "opaque_conditional_count"
            self.assertEqual(factor["base_metrics"][metric], 1)
            self.assertEqual(factor["metrics"][metric], 2)
            self.assertEqual(factor["delta"][metric], 1)
            findings = [item for item in factor["findings"] if item["kind"] == "opaque_conditional"]
            self.assertEqual([item["path"] for item in findings], ["app.py"])
            self.assertEqual(findings[0]["evidence"]["revision"], git("rev-parse", "HEAD"))
            self.assertEqual(findings[0]["evidence"]["primary"]["code"], "a and b and c")


    def test_real_diff_preserves_base_measurements_and_actionable_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def git(*args):
                return subprocess.check_output(["git", *args], cwd=root, text=True).strip()
            git("init", "-q")
            git("config", "user.name", "QCA fixture")
            git("config", "user.email", "qca@example.invalid")
            (root / "app.py").write_text("def run(value):\n    return value\n")
            git("add", ".")
            git("commit", "-qm", "base")
            base = git("rev-parse", "HEAD")
            (root / "app.py").write_text("def run(value, items=[]):\n    items.append(value)\n    return items\n")
            git("add", ".")
            git("commit", "-qm", "head")
            report = analyze_diff(base, "HEAD", path=root)
            self.assertTrue(report.factors["functional_style"]["findings"])
            self.assertTrue(report.factors["contracts"]["surface_changes"]["changed"])
            self.assertEqual(report.factors["end_to_end"]["status"], "not_measured")
            for factor in report.factors.values():
                for key, delta in factor["delta"].items():
                    self.assertEqual(delta, factor["metrics"].get(key, 0) - factor["base_metrics"].get(key, 0))
                for finding in factor["findings"]:
                    if "evidence" in finding:
                        self.assertEqual(finding["evidence"]["revision"], report.identity.head_sha)


if __name__ == "__main__":
    unittest.main()
