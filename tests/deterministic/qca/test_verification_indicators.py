from __future__ import annotations

import ast
import unittest

from qca.indicators.context import AnalysisContext
from qca.indicators import lifecycle, test_effectiveness


class VerificationIndicatorTests(unittest.TestCase):
    def test_assertion_predicates_are_independent_and_keep_suppressed_matches(self) -> None:
        context = AnalysisContext.from_sources(
            {
                "tests/test_candidates.py": (
                    "def test_candidates(self, value):\n"
                    "    literal = True\n"
                    "    assert True\n"
                    "    assert value == value\n"
                    "    assigned = \"ready\"\n"
                    "    self.assertEqual(assigned, \"ready\")\n"
                    "    self.assertIn(\"| report |\", \"report\")\n"
                )
            }
        )
        tree = context.tree("tests/test_candidates.py", "verification")
        assert tree is not None
        assertions = [node for node in ast.walk(tree) if isinstance(node, (ast.Assert, ast.Call))]
        literal = next(node for node in assertions if isinstance(node, ast.Assert) and isinstance(node.test, ast.Constant))
        tautological = next(node for node in assertions if isinstance(node, ast.Assert) and isinstance(node.test, ast.Compare))
        string_expected = next(
            node
            for node in assertions
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "assertEqual"
        )
        formatted = next(
            node
            for node in assertions
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "assertIn"
        )
        readbacks = test_effectiveness.assignment_readbacks(tree)

        self.assertTrue(test_effectiveness.literal_assertion(literal))
        self.assertFalse(test_effectiveness.tautological_assertion(literal))
        self.assertTrue(test_effectiveness.tautological_assertion(tautological))
        self.assertTrue(test_effectiveness.string_expected_assertion(string_expected))
        self.assertFalse(test_effectiveness.formatted_literal_assertion(string_expected))
        self.assertTrue(test_effectiveness.formatted_literal_assertion(formatted))
        self.assertTrue(test_effectiveness.assignment_readback(string_expected, readbacks))
        self.assertEqual(
            test_effectiveness.candidate_kinds(string_expected, readbacks),
            ("assignment_readback", "string_expected_assertion"),
        )
        self.assertEqual(
            test_effectiveness.assertion_candidate_kind(string_expected, readbacks),
            "assignment_readback",
        )
        self.assertEqual(
            test_effectiveness.candidate_kinds(formatted),
            ("string_expected_assertion", "formatted_literal_assertion"),
        )
        self.assertEqual(
            test_effectiveness.assertion_candidate_kind(formatted),
            "formatted_literal_assertion",
        )

    def test_private_surface_predicates_distinguish_local_test_helpers(self) -> None:
        context = AnalysisContext.from_sources(
            {
                "pkg/app.py": "def _hidden():\n    return 1\n",
                "tests/test_app.py": (
                    "from pkg.app import _hidden\n"
                    "def _local_helper():\n    return 1\n"
                    "def test_app():\n"
                    "    _hidden()\n"
                    "    _local_helper()\n"
                ),
            }
        )
        tree = context.tree("tests/test_app.py", "verification")
        assert tree is not None
        imports = [node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
        self.assertEqual(len(imports), 1)
        self.assertTrue(test_effectiveness.private_import(imports[0]))
        hidden_call = next(node for node in calls if isinstance(node.func, ast.Name) and node.func.id == "_hidden")
        local_call = next(node for node in calls if isinstance(node.func, ast.Name) and node.func.id == "_local_helper")
        self.assertTrue(test_effectiveness.private_helper_call(hidden_call, {"_local_helper"}))
        self.assertFalse(test_effectiveness.private_helper_call(local_call, {"_local_helper"}))

    def test_private_import_uses_available_source_classification(self) -> None:
        context = AnalysisContext.from_sources({
            "pkg/test_engine.py": "def _hidden():\n    pass\ndef _other():\n    pass\n",
            "pkg/testing/support.py": "def _shared():\n    pass\n",
            "pkg/test_helpers.py": "import pytest\ndef _fixture():\n    pass\n",
            "tests/support.py": "def _fixture():\n    pass\n",
            "pkg/test_mixed.py": "def _ambiguous():\n    pass\n",
            "pkg/test_mixed.pyi": "def test_contract() -> None: ...\n",
            "tests/test_imports.py": (
                "from pkg.test_engine import _hidden, _other\n"
                "from pkg.testing.support import _shared\n"
                "from pkg.test_helpers import _fixture\n"
                "from tests.support import _fixture\n"
                "from pkg.test_mixed import _ambiguous\n"
                "from external.test_missing import _unknown\n"
                "from .support import _fixture\n"
            ),
        })
        factor = test_effectiveness.analyze(context)
        imports = [item for item in factor["findings"] if item["kind"] == "private_import"]
        self.assertEqual(factor["metrics"]["private_import_count"], 3)
        self.assertEqual(
            [item["expression"] for item in imports],
            ["from pkg.test_engine import _hidden, _other", "from pkg.testing.support import _shared"],
        )

    def test_analyze_uses_verification_parse_family_and_emits_exact_blocks(self) -> None:
        context = AnalysisContext.from_sources(
            {
                "tests/test_sample.py": (
                    "def test_sample():\n"
                    "    value: int = 3\n"
                    "    assert value == 3\n"
                ),
                "types.pyi": "value: int\n",
                "UPPER.PY": "def typed():\n    return 1\n",
                "broken.py": "def broken(:\n    pass\n",
                "notes.txt": "assert True\n",
            },
            revision="head-ref",
        )
        factor = test_effectiveness.analyze(context)
        candidate = next(item for item in factor["findings"] if item["kind"] == "assignment_readback")
        self.assertIn("UPPER.PY", context.paths("verification"))
        self.assertNotIn("UPPER.PY", context.paths("structure"))
        self.assertEqual(factor["metrics"]["python_file_count"], 4)
        self.assertEqual(factor["metrics"]["parse_error_count"], 0)
        self.assertEqual(candidate["indicator"], {
            "id": "verification.test_effectiveness.assignment_readback",
            "version": "4",
        })
        self.assertEqual(candidate["evidence"]["revision"], "head-ref")
        self.assertEqual(candidate["evidence"]["primary"]["code"], "assert value == 3")
        self.assertEqual(candidate["evidence"]["related"][0]["code"], "value: int = 3")
        self.assertEqual(lifecycle.analyze(context)["metrics"]["parse_error_count"], 1)

    def test_review_questions_match_private_calls_imports_and_assertions(self) -> None:
        context = AnalysisContext.from_sources({
            "tests/test_sample.py": (
                "from package import _private\n"
                "def test_sample(fixture):\n"
                "    fixture._prepare()\n"
                "    value = 1\n"
                "    assert value == 1\n"
                "    self.assertEqual(result, 'ready')\n"
                "    self.assertIn('| report |', output)\n"
            ),
        })
        findings = test_effectiveness.analyze(context)["findings"]
        questions = {item["kind"]: item["evidence"]["inspection_question"] for item in findings}
        self.assertIn("import", questions["private_import"].lower())
        self.assertIn("call", questions["private_helper_call"].lower())
        self.assertIn("assertion", questions["assignment_readback"].lower())
        self.assertIn("exact text", questions["string_expected_assertion"])
        self.assertIn("presentation", questions["formatted_literal_assertion"])
        for item in findings:
            if item["kind"] in {"private_import", "private_helper_call"}:
                self.assertIn("inspect its target", item["reason"])

    def test_lifecycle_sites_use_exact_nodes_and_bounded_details(self) -> None:
        source = "def start():\n    pass\nstart()\n"
        context = AnalysisContext.from_sources({"app.py": source}, revision="head-ref")
        factor = lifecycle.analyze(context)
        self.assertEqual(factor["metrics"]["start_definition_count"], 1)
        self.assertEqual(factor["metrics"]["start_call_count"], 1)
        self.assertEqual(len(factor["findings"]), 2)
        self.assertTrue(all(item["indicator"]["id"] == "verification.lifecycle.effect_site" for item in factor["findings"]))
        self.assertEqual(factor["findings"][0]["evidence"]["primary"]["code"], "def start():\n    pass")


if __name__ == "__main__":
    unittest.main()
