from __future__ import annotations

import ast
import json
import unittest

from qca.indicators import (
    INDICATOR_REGISTRY,
    AnalysisContext,
    IndicatorSpec,
    LocatedNode,
    build_evidence,
    enrich_finding,
)
from qca.indicators import contracts
from qca.indicators import coupling
from qca.indicators import functional_style
from qca.indicators import functionality
from qca.indicators import lifecycle
from qca.indicators import patterns
from qca.indicators import redundancy
from qca.indicators import test_effectiveness


class IndicatorContextTests(unittest.TestCase):
    def test_normalizes_paths_and_caches_same_parse_mode(self) -> None:
        context = AnalysisContext.from_sources(
            {
                "./pkg\\module.py": "value = 1\r\n",
                "pkg/stub.pyi": "value: int\n",
            }
        )

        self.assertEqual(context.sources, {"pkg/module.py": "value = 1\n", "pkg/stub.pyi": "value: int\n"})
        structure_tree = context.tree("pkg/module.py", "structure")
        coupling_tree = context.tree("pkg/module.py", "coupling")
        self.assertIsNotNone(structure_tree)
        self.assertIs(structure_tree, coupling_tree)
        self.assertEqual(context.paths("structure"), ("pkg/module.py",))
        self.assertEqual(context.paths("verification"), ("pkg/module.py", "pkg/stub.pyi"))
        self.assertIsNotNone(context.tree("pkg/stub.pyi", "verification"))
        self.assertIsNone(context.tree("pkg/stub.pyi", "structure"))

    def test_unicode_ast_byte_columns_still_slice_exact_code(self) -> None:
        source = "é = 1; result = é\n"
        context = AnalysisContext.from_sources({"unicode.py": source})
        tree = context.tree("unicode.py", "structure")
        assert tree is not None
        result = next(node for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id == "result")

        location = context.location("unicode.py", result, "structure")
        self.assertIsNotNone(location)
        assert location is not None
        self.assertEqual(location.start_col, len("é = 1; ".encode("utf-8")))
        self.assertEqual(location.start_char_col, len("é = 1; "))
        self.assertEqual(context.source_block("unicode.py", result, "structure").code, "result")  # type: ignore[union-attr]

    def test_nested_symbol_selection_uses_parent_index(self) -> None:
        source = (
            "class Outer:\n"
            "    def method(self):\n"
            "        def inner():\n"
            "            return 1\n"
            "        return inner()\n"
        )
        context = AnalysisContext.from_sources({"nested.py": source})
        tree = context.tree("nested.py", "structure")
        assert tree is not None
        inner = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "inner")
        returned = next(node for node in ast.walk(inner) if isinstance(node, ast.Return))

        parent = context.parent("nested.py", returned, "structure")
        self.assertIsInstance(parent, ast.FunctionDef)
        self.assertEqual(parent.name, "inner")  # type: ignore[union-attr]
        self.assertEqual(context.enclosing_symbols("nested.py", returned, "structure"), ("Outer", "method", "inner"))
        self.assertEqual(context.enclosing_symbols("nested.py", inner, "structure"), ("Outer", "method", "inner"))

    def test_nodes_at_retains_multiple_nodes_on_one_line(self) -> None:
        context = AnalysisContext.from_sources({"same_line.py": "result = left + right\n"})
        nodes = context.nodes_at("same_line.py", 1, family="structure")
        names = [node.id for node in nodes if isinstance(node, ast.Name)]
        self.assertEqual(names, ["result", "left", "right"])
        self.assertGreater(len(nodes), len(names))

        evidence = build_evidence(context, path="same_line.py", pattern={"line": 1})
        self.assertIsNone(evidence["primary"])
        self.assertEqual(evidence["primary_reason"], "no_primary_node_supplied")

    def test_syntax_errors_are_family_scoped_and_other_files_survive(self) -> None:
        context = AnalysisContext.from_sources(
            {
                "broken.py": "def broken(:\n",
                "ok.py": "def okay():\n    return 1\n",
                "broken.pyi": "def broken(:\n",
            }
        )
        errors = context.syntax_errors("structure")
        self.assertEqual(set(errors), {"broken.py"})
        self.assertEqual(errors["broken.py"].line, 1)
        self.assertEqual(set(context.trees("structure")), {"ok.py"})
        verification_errors = context.syntax_errors("verification")
        self.assertEqual(set(verification_errors), {"broken.py", "broken.pyi"})
        self.assertEqual(set(context.trees("verification")), {"ok.py"})

    def test_exact_multiline_block_and_rich_finding_evidence_are_json_safe(self) -> None:
        source = "def run(value):\n    total = value + 1\n    return total\n"
        context = AnalysisContext.from_sources({"app.py": source}, revision="abc123")
        tree = context.tree("app.py", "structure")
        assert tree is not None
        function = tree.body[0]
        assignment = function.body[0]

        finding = enrich_finding(
            {"path": "app.py", "line": 2, "kind": "candidate", "message": "Inspect."},
            context=context,
            indicator_id="structure.candidate",
            indicator_version="1",
            path="app.py",
            node=assignment,
            family="structure",
            related_nodes=[LocatedNode("app.py", function, "structure")],
            pattern={"operator": "+", "count": 1},
            uncertainty=["Static syntax does not establish runtime intent."],
            inspection_question="Does this expression preserve the owning boundary's contract?",
        )

        self.assertEqual(finding["indicator"], {"id": "structure.candidate", "version": "1"})
        evidence = finding["evidence"]
        self.assertEqual(evidence["revision"], "abc123")
        self.assertEqual(evidence["primary"]["code"], "total = value + 1")
        self.assertEqual(evidence["related"][0]["code"], "def run(value):\n    total = value + 1\n    return total")
        self.assertEqual(evidence["pattern"], {"operator": "+", "count": 1})
        self.assertEqual(evidence["uncertainty"], ["Static syntax does not establish runtime intent."])
        json.dumps(finding)


class IndicatorRegistryTests(unittest.TestCase):
    def test_registry_lists_shipped_detector_identities(self) -> None:
        expected = {"structure.syntax_error"}
        for module in (
            redundancy,
            patterns,
            functional_style,
            functionality,
            coupling,
            contracts,
            test_effectiveness,
            lifecycle,
        ):
            expected.add(module.INDICATOR_ID)
            expected.update(module.DETECTOR_IDS.values())
        self.assertEqual(set(INDICATOR_REGISTRY), expected)
        for indicator_id, spec in INDICATOR_REGISTRY.items():
            self.assertIsInstance(spec, IndicatorSpec)
            self.assertEqual(spec.id, indicator_id)
            self.assertEqual(spec.version, "1")


if __name__ == "__main__":
    unittest.main()
