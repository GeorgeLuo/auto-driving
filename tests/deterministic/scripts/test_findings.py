import unittest

from qca import evaluate
from qca.text_evaluators import python_patterns
from scripts.validation.findings import inspection_records
from scripts.validation.text_evaluators import retired_vocabulary


class FindingTests(unittest.TestCase):
    def test_evaluation_accepts_arbitrary_text_and_reports_input_spans(self) -> None:
        text = "--- a/note\n+++ b/note\n+old " + "SNAP" + "SHOT" + "\n"
        results = evaluate(text, [retired_vocabulary])
        self.assertEqual(len(results), 1)
        span = results[0]["location"]
        self.assertEqual(text[span["start"]:span["end"]], "SNAP" + "SHOT")
        self.assertEqual(results[0]["type"], "vocabulary.retired")

    def test_qca_python_evaluator_points_into_original_unicode_text(self) -> None:
        text = "# café\r\ndef choose(a, b, c):\r\n    if a and b and c:\r\n        return 1\r\n"
        result = next(item for item in evaluate(text, [python_patterns]) if item["type"] == "qca.opaque_conditional")
        span = result["location"]
        self.assertEqual(text[span["start"]:span["end"]], "a and b and c")
        with self.assertRaises(SyntaxError):
            evaluate("def broken(", [python_patterns])

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
