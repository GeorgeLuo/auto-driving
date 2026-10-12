from __future__ import annotations

import ast
import json
import unittest

from qca.factors.coupling import analyze_coupling, analyze_coupling_context
from qca.indicators import AnalysisContext
from qca.indicators import contracts
from qca.indicators import coupling


class CouplingIndicatorTests(unittest.TestCase):
    def test_cycle_finding_cites_related_import_sites_without_inventing_path(self) -> None:
        context = AnalysisContext.from_sources(
            {
                "pkg/a.py": "from . import b\n",
                "pkg/b.py": "from . import a\n",
            },
            revision="head-1",
        )

        result = coupling.analyze(context)
        finding = next(item for item in result["findings"] if item["kind"] == "cycle")
        evidence = finding["evidence"]

        self.assertEqual(finding["indicator"], {"id": "coupling.graph.cycle", "version": "1"})
        self.assertEqual(evidence["revision"], "head-1")
        self.assertIsNone(evidence["primary"])
        self.assertEqual(
            evidence["primary_reason"], "graph_aggregate_no_single_source_range"
        )
        self.assertEqual(
            [block["code"] for block in evidence["related"]],
            ["from . import b", "from . import a"],
        )
        self.assertEqual(evidence["pattern"]["members"], ["pkg/a.py", "pkg/b.py"])
        self.assertNotIn(" -> ", finding["message"])
        json.dumps(result)

    def test_fan_out_finding_has_exact_related_import_nodes(self) -> None:
        context = AnalysisContext.from_sources(
            {
                "app.py": "import b\nimport c\nimport d\n",
                "b.py": "VALUE = 1\n",
                "c.py": "VALUE = 2\n",
                "d.py": "VALUE = 3\n",
            }
        )

        result = coupling.analyze(context)
        finding = next(item for item in result["findings"] if item["kind"] == "fan_out")

        self.assertIsNone(finding["evidence"]["primary"])
        self.assertEqual(
            [block["code"] for block in finding["evidence"]["related"]],
            ["import b", "import c", "import d"],
        )
        self.assertEqual(
            finding["evidence"]["pattern"],
            {"direction": "out", "module": "app.py", "value": 3},
        )

    def test_import_finding_and_contracts_cite_the_detected_ast_nodes(self) -> None:
        source = """import argparse

parser = argparse.ArgumentParser()
subparsers = parser.add_subparsers()
subparsers.add_parser('run')
parser.add_argument('-v', '--verbose', action='store_true')

async def execute(value, /, fallback=None, *, limit=3):
    return {'status': value, 'count': limit}
"""
        context = AnalysisContext.from_sources({"cli/app.py": source}, revision="r7")

        contracts_result = contracts.analyze(context)
        findings = contracts_result["findings"]
        callable_finding = next(item for item in findings if item["kind"] == "callable_contract")
        return_finding = next(item for item in findings if item["kind"] == "return_shape")
        argument_finding = next(item for item in findings if item["kind"] == "cli_argument")
        command_finding = next(item for item in findings if item["kind"] == "cli_command")

        self.assertEqual(
            callable_finding["evidence"]["primary"]["code"],
            "async def execute(value, /, fallback=None, *, limit=3):\n"
            "    return {'status': value, 'count': limit}",
        )
        self.assertEqual(
            return_finding["evidence"]["primary"]["code"],
            "{'status': value, 'count': limit}",
        )
        self.assertEqual(argument_finding["evidence"]["primary"]["code"],
                         "parser.add_argument('-v', '--verbose', action='store_true')")
        self.assertEqual(command_finding["evidence"]["primary"]["code"],
                         "subparsers.add_parser('run')")
        self.assertEqual(callable_finding["evidence"]["revision"], "r7")
        self.assertEqual(
            contracts_result["surface"]["cli/app.py::execute"]["return_dict_keys"],
            [["count", "status"]],
        )
        json.dumps(contracts_result)

        import_result = coupling.analyze(context)
        import_finding = next(
            item for item in import_result["findings"] if item["kind"] == "external_import"
        )
        self.assertEqual(import_finding["evidence"]["primary"]["code"], "import argparse")

    def test_syntax_errors_are_preserved_by_standalone_modules_and_wrapper(self) -> None:
        sources = {
            "broken.py": "def broken(:\n    pass\n",
            "ok.py": "VALUE = 1\n",
        }
        context = AnalysisContext.from_sources(sources, revision="working-tree")

        for module in (coupling, contracts):
            result = module.analyze(context)
            finding = next(item for item in result["findings"] if item["kind"] == "syntax_error")
            self.assertEqual(finding["path"], "broken.py")
            self.assertIsNone(finding["evidence"]["primary"])
            self.assertEqual(
                finding["evidence"]["primary_reason"], "syntax_error_no_ast_node"
            )
            self.assertEqual(finding["evidence"]["revision"], "working-tree")
            self.assertTrue(result["limitations"])

        wrapped = analyze_coupling(sources, revision="working-tree")
        self.assertEqual(
            sum(item["kind"] == "syntax_error" for item in wrapped["coupling"]["findings"]),
            1,
        )
        self.assertEqual(
            sum(item["kind"] == "syntax_error" for item in wrapped["contracts"]["findings"]),
            1,
        )
        self.assertEqual(
            analyze_coupling_context(context)["coupling"]["metrics"]["module_count"], 1
        )

    def test_inventory_root_init_relative_import_does_not_crash(self) -> None:
        context = AnalysisContext.from_sources(
            {
                "__init__.py": "from .analyzer import analyze_tree\n",
                "analyzer.py": "def analyze_tree():\n    return None\n",
            }
        )

        result = coupling.analyze(context)

        self.assertEqual(result["metrics"]["module_count"], 2)
        self.assertEqual(result["metrics"]["edge_count"], 1)
        self.assertEqual(result["metrics"]["external_import_count"], 0)
        self.assertEqual(coupling._module("__init__.py"), "")
        self.assertEqual(coupling._package("__init__.py"), "")
        json.dumps(result)

    def test_legacy_helpers_remain_independently_callable(self) -> None:
        tree = ast.parse("def run(value=1):\n    return value\n")
        function = tree.body[0]

        self.assertEqual(contracts._signature(function)["defaults"], {"value": "1"})
        self.assertEqual(
            coupling._lookup_resolution("pkg.missing", {"pkg": "pkg/__init__.py"})[
                "resolution"
            ],
            "ancestor_fallback",
        )


if __name__ == "__main__":
    unittest.main()
