from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from qca import analyze_diff


class FactorReportTests(unittest.TestCase):


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
