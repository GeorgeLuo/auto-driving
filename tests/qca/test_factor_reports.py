from __future__ import annotations

import html
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from qca import AnalyzerConfig, analyze_diff, analyze_tree, report_to_dict
from qca.render import render_html


class FactorReportTests(unittest.TestCase):
    def test_excluded_source_does_not_contribute_structural_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text("def run():\n    return 1\n")
            (root / "ignored.py").write_text("def broken(items=[]):\n    pass\n")
            (root / "workbench.html").write_text("<h1>Playback</h1>")
            report = analyze_tree(root, config=AnalyzerConfig(excluded_globs=("ignored.py",)))
            for factor in report.factors.values():
                self.assertFalse(any(item.get("path") == "ignored.py" for item in factor["findings"]))
            source = next(item for item in report.source_inventory if item.path == "workbench.html")
            self.assertEqual(source.source_class, "production")
            self.assertIn("workbench.html", report.head.unsupported_files)
            self.assertEqual(report.factors["ui_behavior"]["status"], "not_measured")

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

    def test_html_embeds_the_exact_record_and_escapes_source_text(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text('def view():\n    return "<script>alert(1)</script>"\n')
            payload = report_to_dict(analyze_tree(root))
            rendered = render_html(payload)
            raw = rendered.split("Complete JSON record</summary><pre>", 1)[1].split("</pre>", 1)[0]
            self.assertEqual(json.loads(html.unescape(raw)), payload)
            self.assertNotIn("<script>", rendered)


if __name__ == "__main__":
    unittest.main()
