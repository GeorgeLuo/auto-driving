from __future__ import annotations

import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from scripts.validation.check import METRIC_RULES, baseline_entries, compare_baseline, findings
from scripts.validation.qca_summary import render_summary, section
from qca import analyze_sources
from qca.analyzer import DiffMetrics


class RuffBaselineTests(unittest.TestCase):
    def test_same_error_in_another_function_is_not_baselined(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "module.py"
            path.write_text("def old():\n    return missing\n\ndef new():\n    return missing\n")
            diagnostics = [
                {"filename": str(path), "code": "F821", "message": "Undefined name `missing`",
                 "location": {"row": line, "column": 12}}
                for line in (2, 5)
            ]
            items = findings(diagnostics, root)
            added, _ = compare_baseline(items, baseline_entries(items[:1]))
            self.assertEqual([item.key[2] for item in added], ["new"])

    def test_duplicate_diagnostic_needs_its_own_baseline_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "module.py"
            path.write_text("missing\nmissing\n")
            diagnostics = [
                {"filename": str(path), "code": "F821", "message": "Undefined name `missing`",
                 "location": {"row": line, "column": 1}}
                for line in (1, 2)
            ]
            items = findings(diagnostics, root)
            added, retained = compare_baseline(items, baseline_entries(items[:1]))
            self.assertEqual(len(added), 1)
            self.assertEqual(retained, baseline_entries(items[:1]))

    def test_metric_improvements_retire_old_limits_and_cannot_reaccept_growth(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "module.py"
            path.write_text("def process():\n    pass\n")
            for rule in sorted(METRIC_RULES):
                with self.subTest(rule=rule):
                    def measured(count):
                        return findings([{
                            "filename": str(path), "code": rule, "message": f"Too much ({count} > 10)",
                            "location": {"row": 1, "column": 1},
                        }], root)

                    added, retained = compare_baseline(measured(11), baseline_entries(measured(12)))
                    self.assertEqual(added, [])
                    self.assertEqual(retained[0]["limits"], [11])
                    self.assertEqual(len(compare_baseline(measured(12), retained)[0]), 1)
                    self.assertEqual(compare_baseline([], retained), ([], []))

    def test_pruning_keeps_only_matched_diagnostics_without_accepting_new_debt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "module.py"
            path.write_text("missing\nother_missing\n")
            items = findings([
                {"filename": str(path), "code": "F821", "message": f"Undefined name `{name}`",
                 "location": {"row": line, "column": 1}}
                for line, name in enumerate(("missing", "other_missing"), 1)
            ], root)
            added, retained = compare_baseline(items[1:], baseline_entries(items[:1]))
            self.assertEqual(added, items[1:])
            self.assertEqual(retained, [])


class QcaSummaryTests(unittest.TestCase):
    def test_filters_routine_observations_and_labels_existing_candidates(self) -> None:
        report = analyze_sources({
            "module.py": "def choose(a, b, c):\n    if a and b and c:\n        raise ValueError('bad')\n    return 1\n",
        })
        report.diff = DiffMetrics(changed_files=["module.py"], changed_callables=[
            {"path": "module.py", "before": None, "after": asdict(report.head.callables[0]), "qualified_name": "choose"},
        ])
        summary = render_summary(report)
        self.assertIn("module.py:2", summary)
        self.assertIn("may predate this change", summary)
        self.assertNotIn("Raise statement observed", summary)
        self.assertNotIn("Public callable", summary)

    def test_untouched_functions_and_standard_library_edges_stay_out_of_summary(self) -> None:
        report = analyze_sources({"module.py": (
            "def changed(a, b, c):\n    if a and b and c:\n        return b\n    return a\n\n"
            "def untouched(x, y, z):\n    if x or y or z:\n        return x\n    return z\n"
        )})
        changed = next(item for item in report.head.callables if item.qualified_name == "changed")
        report.diff = DiffMetrics(changed_callables=[
            {"path": "module.py", "before": None, "after": asdict(changed), "qualified_name": "changed"},
        ], new_import_edges=["module.py->os", "module.py->autonomy.vehicle", "module.py->.shared"])
        summary = render_summary(report)
        self.assertIn("module.py:2", summary)
        self.assertNotIn("module.py:7", summary)
        self.assertNotIn("module.py → os", summary)
        self.assertIn("module.py → autonomy.vehicle", summary)
        self.assertIn("module.py → .shared", summary)

    def test_only_growing_functions_above_thresholds_are_highlighted(self) -> None:
        report = analyze_sources({"module.py": "def choose():\n    return 1\n"})
        measured = {"start_line": 1, "cyclomatic_complexity": 11, "logical_loc": 12, "max_nesting": 1}
        report.diff = DiffMetrics(changed_callables=[
            {"path": "module.py", "qualified_name": "grew", "before": {**measured, "cyclomatic_complexity": 10}, "after": measured},
            {"path": "module.py", "qualified_name": "improved", "before": {**measured, "cyclomatic_complexity": 12}, "after": measured},
            {"path": "module.py", "qualified_name": "removed", "before": measured, "after": None},
        ])
        summary = render_summary(report)
        self.assertIn("`grew`", summary)
        self.assertNotIn("`improved`", summary)
        self.assertNotIn("`removed`", summary)

    def test_bounded_summary_reports_total_and_omission(self) -> None:
        output = "\n".join(section("Candidates", [str(i) for i in range(25)]))
        self.assertIn("Candidates (25)", output)
        self.assertIn("Showing 20", output)
        self.assertNotIn("- 24", output)


if __name__ == "__main__":
    unittest.main()
