from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from qca.evaluation import SCHEMA, finding, location, validate_finding
from scripts.validation.check import exit_status, render_summary
from scripts.validation.evaluators import basedpyright_findings, ruff_findings, vocabulary

RETIRED = "snap" + "shot"


def ruff_diagnostic(root: Path, code: str | None, message: str, row: int = 3) -> dict:
    return {
        "filename": str(root / "cli" / "module.py"), "code": code, "message": message,
        "location": {"row": row, "column": 5}, "end_location": {"row": row + 1, "column": 1},
    }


class ToolFindingTests(unittest.TestCase):
    def test_ruff_metric_diagnostics_carry_a_measure_and_syntax_errors_a_rule(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            findings = ruff_findings([
                ruff_diagnostic(root, "C901", "`choose` is too complex (13 > 10)"),
                ruff_diagnostic(root, None, "Expected an expression"),
            ], root)
        for item in findings:
            validate_finding(item)
        self.assertEqual(findings[0]["location"], location("cli/module.py", 3, 4))
        self.assertEqual(findings[0]["measure"], {"value": 13, "limit": 10})
        self.assertEqual((findings[1]["rule"], findings[1]["measure"]), ("invalid-syntax", None))
        self.assertTrue(all(item["status"] == "blocking" for item in findings))

    def test_type_errors_block_warnings_ask_and_paths_outside_scope_are_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def diagnostic(path: str, severity: str) -> dict:
                return {"file": str(root / path), "severity": severity, "message": "Type `Any` is not allowed",
                        "rule": "reportExplicitAny", "range": {"start": {"line": 0}, "end": {"line": 1}}}

            findings = basedpyright_findings(
                [diagnostic("cli/a.py", "error"), diagnostic("cli/b.py", "warning"), diagnostic("qca/c.py", "error")],
                root, ["cli"],
            )
        self.assertEqual(
            [(item["location"]["path"], item["location"]["line"], item["status"]) for item in findings],
            [("cli/a.py", 1, "blocking"), ("cli/b.py", 1, "review")],
        )

    def test_retired_vocabulary_is_found_in_text_outside_deprecated_documents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "docs" / "deprecated").mkdir(parents=True)
            (root / "docs" / "deprecated" / "old.md").write_text(f"a {RETIRED}\n")
            (root / "notes.md").write_text(f"fine\nTake a {RETIRED.upper()} here\n")
            (root / "image.png").write_bytes(b"\x89PNG\xff" + RETIRED.encode())
            findings = vocabulary(root, ["docs/deprecated/old.md", "notes.md", "image.png"])
        self.assertEqual([item["location"] for item in findings], [location("notes.md", 2)])
        self.assertEqual(findings[0]["evidence"], {"term": RETIRED.upper()})


class SummaryTests(unittest.TestCase):
    def document(self, findings: list, evaluations: list) -> dict:
        context = {"form": "change", "base": "a" * 40, "head": None, "paths": ["cli/a.py"]}
        return {"schema": SCHEMA, "context": context, "evaluations": evaluations, "findings": findings}

    def test_summary_separates_blocking_findings_from_review_questions_and_bounds_them(self) -> None:
        blocking = finding("ruff", "F821", location("cli/a.py", 2), "Undefined?", status="blocking")
        review = [finding("qca", "structure.x", location("cli/a.py", line), "Clearer?") for line in range(1, 53)]
        evaluations = [
            {"evaluator": "ruff", "required": True, "status": "completed", "findings": 1},
            {"evaluator": "qca", "required": False, "status": "failed", "error": "RuntimeError: a | b"},
        ]
        summary = render_summary(self.document([blocking, *review], evaluations))
        self.assertIn("to the working tree, 1 changed files", summary)
        self.assertIn("| qca | failed (informational): RuntimeError: a \\| b |", summary)
        self.assertIn("### Blocking (1)\n\n- `cli/a.py:2` ruff `F821`: Undefined?", summary)
        self.assertIn("### Review questions (52)", summary)
        self.assertIn("Showing 50; findings.json has all 52.", summary)
        self.assertNotIn("`cli/a.py:51`", summary)

    def test_exit_status_fails_on_blocking_findings_and_required_failures_only(self) -> None:
        blocking = finding("ruff", "F821", location("cli/a.py", 2), "Undefined?", status="blocking")
        review = finding("qca", "structure.x", location("cli/a.py", 2), "Clearer?")

        def failed(required: bool) -> dict:
            return {"evaluator": "tool", "required": required, "status": "failed", "error": "crashed"}

        self.assertEqual(exit_status(self.document([review], [failed(False)])), 0)
        self.assertEqual(exit_status(self.document([blocking, review], [])), 1)
        self.assertEqual(exit_status(self.document([], [failed(True)])), 2)


if __name__ == "__main__":
    unittest.main()
