from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from qca.evaluation import SCHEMA, finding, location, validate_finding
from scripts.validation.check import exit_status, render_summary
from scripts.validation.evaluators import basedpyright_findings, import_contract_findings, ruff_findings, vocabulary
from scripts.validation.plugin_state import main as plugin_state_main, retained_state

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

    def test_type_errors_block_and_warnings_ask(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def diagnostic(path: str, severity: str) -> dict:
                return {"file": str(root / path), "severity": severity, "message": "Type `Any` is not allowed",
                        "rule": "reportExplicitAny", "range": {"start": {"line": 0}, "end": {"line": 1}}}

            findings = basedpyright_findings([diagnostic("cli/a.py", "error"), diagnostic("cli/b.py", "warning")], root)
        self.assertEqual(
            [(item["location"]["path"], item["location"]["line"], item["status"]) for item in findings],
            [("cli/a.py", 1, "blocking"), ("cli/b.py", 1, "review")],
        )

    def test_a_broken_import_contract_starts_where_its_chain_starts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pkg" / "shared").mkdir(parents=True)
            (root / "pkg" / "shared" / "__init__.py").write_text("from pkg import bridge\n")
            (root / "pkg" / "bridge.py").write_text("from pkg import plugin\n")
            [item] = import_contract_findings([{
                "contract": "shared", "name": "Shared code does not depend on plugins", "guidance": "",
                "chain": [
                    {"importer": "pkg.shared", "imported": "pkg.bridge", "line": 1},
                    {"importer": "pkg.bridge", "imported": "pkg.plugin", "line": 1},
                ],
            }], root)
        validate_finding(item)
        self.assertEqual((item["tool"], item["rule"], item["status"]), ("import-linter", "shared", "blocking"))
        self.assertEqual(item["location"], location("pkg/shared/__init__.py", 1))
        self.assertEqual(item["related"], [location("pkg/bridge.py", 1)])
        self.assertEqual(item["evidence"]["chain"], ["pkg.shared -> pkg.bridge", "pkg.bridge -> pkg.plugin"])
        self.assertNotIn("guidance", item["evidence"])

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


PLUGIN = """
from copy import copy

COUNT = 0


class Counter:
    plugin_id = "counter"

    def __init__(self, step=1):
        self.step = step
        self.seen = []

    def propose(self, context):
        self._remember(context)
        return copy(self)._scratch(context)

    def status(self, shared_memory):
        global COUNT
        COUNT += 1

    def _remember(self, context):
        self.last, self.frames = context.frame_id, 1
        self.seen.append(context.frame_id)
        setattr(self, "kept", context.frame_id)
        setattr(self, "noted", value=context.frame_id)
        delattr(self, "dropped")
        object.__setattr__(self, "held", context.frame_id)
        object.__delattr__(self, "released")
        self.__setattr__("bound", context.frame_id)
        self.__delattr__("cleared")

    def _scratch(self, context):
        self.note = context.frame_id  # on a per-call copy: not followed
        return self.note


class Helper:
    def propose(self, context):
        self.last = context
"""


class PluginStateTests(unittest.TestCase):
    def test_writes_to_self_reached_from_a_step_method_are_found_once_per_attribute(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "plugins").mkdir()
            (root / "shared").mkdir()
            (root / "plugins" / "counter.py").write_text(PLUGIN)
            (root / "shared" / "worker.py").write_text(PLUGIN)
            findings = retained_state(root, ["plugins/counter.py", "shared/worker.py"])
        found = {(item["evidence"]["method"], item["evidence"]["attribute"]) for item in findings}
        self.assertEqual(found, {("status", "global"), ("_remember", "last"), ("_remember", "frames"),
                                 ("_remember", "seen"), ("_remember", "kept"), ("_remember", "noted"),
                                 ("_remember", "dropped"), ("_remember", "held"), ("_remember", "released"),
                                 ("_remember", "bound"), ("_remember", "cleared")})
        self.assertEqual({item["location"]["path"] for item in findings}, {"plugins/counter.py"})
        self.assertTrue(all(item["status"] == "blocking" for item in findings))

    def test_a_missing_path_exits_with_an_error(self) -> None:
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            status = plugin_state_main(["does-not-exist"])
        self.assertEqual(status, 2)
        self.assertIn("does-not-exist", error.getvalue())


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
