from __future__ import annotations

import ast
import json
import unittest

from qca.factors.structure import analyze_structure
from qca.indicators import functionality, functional_style, patterns, redundancy
from qca.indicators.context import AnalysisContext


class StructureIndicatorTests(unittest.TestCase):
    def test_any_guard_is_a_direct_positive_and_negative_predicate(self) -> None:
        positive = ast.parse("bool(items) and any(check(x) for x in items)", mode="eval").body
        negative = ast.parse("bool(items) and all(items)", mode="eval").body

        self.assertTrue(patterns.any_guard(positive))
        self.assertFalse(patterns.any_guard(negative))

    def test_clone_predicate_is_independently_callable(self) -> None:
        tree = ast.parse(
            "def left(value):\n"
            "    total = value + 1\n"
            "    return total * 2\n"
            "\n"
            "def right(amount):\n"
            "    total = amount + 1\n"
            "    return total * 2\n"
        )
        left, right = [node for node in tree.body if isinstance(node, ast.FunctionDef)]

        self.assertTrue(redundancy.callable_clone(left, right))

    def test_each_family_analyzes_shared_context_with_identity_and_evidence(self) -> None:
        source = (
            "def run(value, items=[]):\n"
            "    items.append(value)\n"
            "    try:\n"
            "        raise ValueError(value)\n"
            "    except:\n"
            "        pass\n"
            "    return items\n"
            "\n"
            "def hook():\n"
            "    pass\n"
            "\n"
            "def twin(value):\n"
            "    total = value + 1\n"
            "    return total * 2\n"
            "\n"
            "def twin_copy(amount):\n"
            "    total = amount + 1\n"
            "    return total * 2\n"
        )
        context = AnalysisContext.from_sources({"app.py": source}, revision="head")

        factors = {
            "redundancy": redundancy.analyze(context),
            "patterns": patterns.analyze(context),
            "functional_style": functional_style.analyze(context),
            "functionality": functionality.analyze(context),
        }
        expected = {
            "redundancy.callable_clone",
            "patterns.bare_except",
            "functional_style.mutable_default",
            "functional_style.mutating_call",
            "functionality.stub",
        }
        observed = {
            finding["indicator"]["id"].removeprefix("structure.")
            for factor in factors.values()
            for finding in factor["findings"]
            if "indicator" in finding
        }
        self.assertTrue(expected <= observed)
        for factor in factors.values():
            for finding in factor["findings"]:
                self.assertEqual(finding["indicator"]["version"], "1")
                self.assertEqual(finding["evidence"]["revision"], "head")
                self.assertIsNotNone(finding["evidence"]["primary"])
        json.dumps(factors)

    def test_redundancy_evidence_retains_branches_and_clone_occurrences(self) -> None:
        source = (
            "def left(value):\n"
            "    if value:\n"
            "        first = 1\n"
            "        second = 2\n"
            "    else:\n"
            "        first = 1\n"
            "        second = 2\n"
            "    return first\n"
            "\n"
            "def right(amount):\n"
            "    if amount:\n"
            "        first = 1\n"
            "        second = 2\n"
            "    else:\n"
            "        first = 1\n"
            "        second = 2\n"
            "    return first\n"
        )
        context = AnalysisContext.from_sources({"app.py": source})
        findings = redundancy.analyze(context)["findings"]

        branch = next(item for item in findings if item["kind"] == "repeated_branch")
        self.assertEqual(
            branch["evidence"]["pattern"]["branches"]["body"],
            ["first = 1", "second = 2"],
        )
        self.assertEqual(
            [item["code"] for item in branch["evidence"]["related"]],
            ["first = 1", "second = 2", "first = 1", "second = 2"],
        )
        clone = next(item for item in findings if item["kind"] == "callable_clone")
        self.assertEqual(len(clone["evidence"]["related"]), 1)
        self.assertEqual(clone["evidence"]["related"][0]["code"].splitlines()[0], "def right(amount):")

    def test_legacy_wrapper_keeps_metrics_and_adds_rich_evidence(self) -> None:
        result = analyze_structure({"broken.py": "def broken(:\n", "stub.py": "def hook():\n    pass\n"})

        self.assertEqual(result["functionality"]["metrics"]["stub_count"], 1)
        self.assertTrue(any(item["kind"] == "syntax_error" for item in result["patterns"]["findings"]))
        syntax = next(item for item in result["patterns"]["findings"] if item["kind"] == "syntax_error")
        self.assertEqual(syntax["indicator"], {"id": "structure.syntax_error", "version": "1"})
        self.assertIsNone(syntax["evidence"]["primary"])
        self.assertEqual(syntax["evidence"]["primary_reason"], "syntax_error_no_ast_node")


if __name__ == "__main__":
    unittest.main()
