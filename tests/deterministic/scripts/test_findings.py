import unittest
from pathlib import Path
from unittest.mock import patch

from qca import evaluate
from qca.evaluation import finding
from scripts.validation import check
from scripts.validation.findings import inspection_records
from scripts.validation.text_evaluators import retired_vocabulary


class FindingTests(unittest.TestCase):
    def test_evaluation_accepts_arbitrary_text_and_reports_input_spans(self) -> None:
        text = "--- a/note\n+++ b/note\n+old " + "SNAP" + "SHOT" + "\n"
        results = evaluate([(retired_vocabulary, text)])
        self.assertEqual(len(results), 1)
        span = results[0]["location"]
        self.assertEqual(text[span["start"]:span["end"]], "SNAP" + "SHOT")
        self.assertEqual(results[0]["type"], "vocabulary.retired")

    def test_evaluation_accepts_native_contexts_and_flattens_evaluator_results(self) -> None:
        context = {"file.py": "one"}
        def first(value):
            return [finding("first", {"path": value["file.py"]}, "Question?", "test", "first", evidence={})]
        def second(value):
            return [finding("second", {"path": value["file.py"]}, "Question?", "test", "second", evidence={})]
        text = "a different format"
        def third(value):
            return [finding("text", {"start": 0, "end": len(value)}, "Question?", "test", "third", evidence={})]
        self.assertEqual([item["type"] for item in evaluate([(first, context), (second, context), (third, text)])],
                         ["first", "second", "text"])

    def test_quality_command_dispatches_all_checks_and_uses_finding_status(self) -> None:
        calls = []
        def evaluator(name, *, blocking=False):
            def run(context):
                calls.append((name, context))
                return [finding(name, {"path": "file.py"}, "Question?", "test", name, evidence={},
                                status="blocking" if blocking else "review")]
            return run
        with patch.object(check, "changed_lines", return_value={"file.py": {1}}), \
             patch.object(check, "evaluate_ruff", evaluator("ruff")), \
             patch.object(check, "evaluate_imports", evaluator("imports")), \
             patch.object(check, "evaluate_types", evaluator("types", blocking=True)), \
             patch.object(check, "evaluate_vocabulary", evaluator("vocabulary")), \
             patch.object(check, "evaluate_inspection", evaluator("inspection")):
            self.assertEqual(check.main([]), 1)
        self.assertEqual([name for name, _ in calls], ["ruff", "imports", "types", "vocabulary", "inspection"])
        self.assertTrue(all(context is calls[0][1] for _, context in calls))

    def test_import_failure_without_findings_cannot_pass(self) -> None:
        with patch.object(check, "check_imports", return_value=1):
            with self.assertRaisesRegex(ValueError, "without findings"):
                check.evaluate_imports(check.QualityContext({}, None))

    def test_engine_failure_does_not_discard_prior_findings(self) -> None:
        def first(_context):
            return [finding("found", {"path": "file.py"}, "Question?", "test", "first", evidence={})]
        def fail(_context):
            raise ValueError("engine failed")
        records = []
        with self.assertRaisesRegex(ValueError, "engine failed"):
            evaluate([(first, "text"), (fail, {})], findings=records)
        self.assertEqual([item["type"] for item in records], ["found"])

    def test_invalid_findings_fail_instead_of_being_silently_accepted(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid shape"):
            evaluate([(lambda _context: [{"type": "missing fields"}], {})])

    def test_invalid_informational_inspection_is_reported_without_blocking(self) -> None:
        def invalid(_output, records, **_options):
            records.append({"type": "missing fields"})
            return []
        context = check.QualityContext({}, Path("unused"))
        with patch.object(check, "inspect_changes", invalid):
            self.assertEqual(check.evaluate_inspection(context), [])
        self.assertIn("invalid finding", context.inspection_errors[0])

    def test_related_pairs_are_one_question_with_both_locations(self) -> None:
        left = {"path": "a.py", "line": 2, "name": "left"}
        right = {"path": "b.py", "line": 4, "name": "right"}
        pair = {"changed": left, "related": right, "shared_calls": ["load", "save"],
                "score": 0.5, "relationship": "same directory"}
        inspection = {"unused": [], "comparisons": [pair, {**pair, "changed": right, "related": left}]}
        findings = inspection_records(inspection)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["location"]["symbol"], "left")
        self.assertEqual(findings[0]["related"][0]["symbol"], "right")
        self.assertEqual(findings[0]["type"], "reuse.related_functions")


if __name__ == "__main__":
    unittest.main()
