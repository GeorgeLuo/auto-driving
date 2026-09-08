from __future__ import annotations

import html
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from qca import AnalyzerConfig, analyze_diff, analyze_sources, analyze_tree, render_markdown, report_to_dict
from qca.factors import measure_factors
from qca.indicators import INDICATOR_REGISTRY
from qca.render import render_html


class FactorReportTests(unittest.TestCase):
    def test_source_evidence_uses_python_physical_lines(self):
        for prefix in ('TEXT = "first\u2028second"\n', '\fTEXT = "page"\n', 'TEXT = "é"\n'):
            with self.subTest(prefix=prefix), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "app.py"
                path.write_text(
                    prefix
                    + "def run():\n    pass\n"
                    + "def test_readback():\n    value = 3\n    assert value == 3\n",
                    encoding="utf-8",
                )
                payload = json.loads(json.dumps(report_to_dict(analyze_tree(path))))
                stub = next(
                    item for item in payload["factors"]["functionality"]["findings"]
                    if item["kind"] == "stub"
                )
                primary = stub["evidence"]["primary"]
                self.assertEqual(primary["code"], "def run():\n    pass")
                self.assertEqual(primary["range"], {
                    "start": {"line": 2, "column": 0, "byte_column": 0},
                    "end": {"line": 3, "column": 8, "byte_column": 8},
                })
                readback = next(
                    item for item in payload["factors"]["test_effectiveness"]["findings"]
                    if item["kind"] == "assignment_readback"
                )
                self.assertEqual(readback["evidence"]["primary"]["code"], "assert value == 3")
                related = readback["evidence"]["related"][0]
                self.assertEqual(related["code"], "value = 3")
                self.assertEqual(related["range"], {
                    "start": {"line": 5, "column": 4, "byte_column": 4},
                    "end": {"line": 5, "column": 13, "byte_column": 13},
                })

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
                for finding in factor["findings"]:
                    if "evidence" in finding:
                        self.assertEqual(finding["evidence"]["revision"], report.identity.head_sha)

    def test_html_embeds_the_exact_record_and_escapes_source_text(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text(
                'def view(items=[]):\n    items.append("<script>alert(1)</script>")\n    return items\n'
            )
            payload = report_to_dict(analyze_tree(root))
            rendered = render_html(payload)
            raw = rendered.split("Complete JSON record</summary><pre>", 1)[1].split("</pre>", 1)[0]
            self.assertEqual(json.loads(html.unescape(raw)), payload)
            self.assertIn("structure.functional_style.mutable_default", rendered)
            self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", rendered)
            self.assertNotIn("<script>", rendered)

    def test_public_findings_use_registered_indicators_and_shared_family_policy(self):
        report = analyze_sources(
            {
                "app.py": (
                    "def run(value, items=[]):\n"
                    "    items.append(value)\n"
                    "    return items\n"
                    "\n"
                    "def hook():\n"
                    "    pass\n"
                ),
                "UPPER.PY": "def hook():\n    pass\n",
                "tests/test_app.py": (
                    "def test_app():\n"
                    "    value = 3\n"
                    "    assert value == 3\n"
                ),
            }
        )
        payload = report_to_dict(report)
        self.assertEqual(payload["factors"]["functionality"]["metrics"]["stub_count"], 1)
        self.assertEqual(payload["factors"]["test_effectiveness"]["metrics"]["python_file_count"], 3)
        self.assertIn("`structure.functionality.stub`", render_markdown(report))
        for factor in payload["factors"].values():
            for finding in factor["findings"]:
                self.assertIn(finding["indicator"]["id"], INDICATOR_REGISTRY)
                self.assertIn("evidence", finding)
                self.assertIsNone(finding["evidence"]["revision"])
        measured = measure_factors({"pkg/a.py": "from . import b\n", "pkg/b.py": "from . import a\n"}, revision="abc")
        cycle = next(item for item in measured["coupling"]["findings"] if item["kind"] == "cycle")
        self.assertEqual(cycle["evidence"]["revision"], "abc")
        self.assertIsNone(cycle["evidence"]["primary"])
        self.assertEqual(
            [block["code"] for block in cycle["evidence"]["related"]],
            ["from . import b", "from . import a"],
        )


if __name__ == "__main__":
    unittest.main()
