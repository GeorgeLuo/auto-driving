from __future__ import annotations

import unittest

from qca.factors.coupling import analyze_coupling


class CouplingFactorTests(unittest.TestCase):
    def test_resolves_relative_and_cli_imports_and_keeps_external_separate(self) -> None:
        result = analyze_coupling(
            {
                "pkg/__init__.py": "from . import entry\n",
                "pkg/entry.py": "from . import json\nfrom pkg import worker\nimport json\n",
                "pkg/json.py": "VALUE = 1\n",
                "pkg/worker.py": "from .json import VALUE\n",
                "cli/automa_cli/app.py": "from automa_cli import helper\n",
                "cli/automa_cli/helper.py": "VALUE = 2\n",
            }
        )
        coupling = result["coupling"]
        edges = {(edge["source"], edge["target"]) for edge in coupling["graph"]["edges"]}
        self.assertIn(("pkg/entry.py", "pkg/json.py"), edges)
        self.assertIn(("pkg/entry.py", "pkg/worker.py"), edges)
        self.assertIn(("cli/automa_cli/app.py", "cli/automa_cli/helper.py"), edges)
        self.assertNotIn("pkg/json.py", {item["name"] for item in coupling["graph"]["unresolved_external"]})
        self.assertIn("json", {item["name"] for item in coupling["graph"]["unresolved_external"]})
        self.assertEqual(coupling["graph"]["fan"]["out"]["pkg/entry.py"], 2)
        self.assertEqual(coupling["metrics"]["cycle_count"], 0)

    def test_reports_cycles_with_deterministic_members_and_locations(self) -> None:
        result = analyze_coupling(
            {
                "a.py": "from . import b\n",
                "b.py": "from . import a\n",
            }
        )
        coupling = result["coupling"]
        self.assertEqual(coupling["graph"]["cycles"], [["a.py", "b.py"]])
        finding = next(item for item in coupling["findings"] if item["kind"] == "cycle")
        self.assertEqual(finding["path"], "a.py")
        self.assertEqual(finding["line"], 1)
        self.assertIn("a.py -> b.py -> a.py", finding["message"])

    def test_contract_surface_captures_signatures_dict_shapes_and_cli_declarations(self) -> None:
        result = analyze_coupling(
            {
                "cli/automa_cli/app.py": """import argparse

parser = argparse.ArgumentParser()
subparsers = parser.add_subparsers()
subparsers.add_parser('run')
parser.add_argument('-v', '--verbose', action='store_true', default=False)

async def execute(value, /, fallback=None, *, limit=3):
    return {'status': value, 'count': limit}
""",
            }
        )
        contracts = result["contracts"]
        surface = contracts["surface"]
        callable_contract = surface["cli/automa_cli/app.py::execute"]
        self.assertTrue(callable_contract["async"])
        self.assertEqual(callable_contract["positional_only"], ["value"])
        self.assertEqual(callable_contract["defaults"], {"fallback": "None"})
        self.assertEqual(callable_contract["kwonly"], ["limit"])
        self.assertEqual(callable_contract["return_dict_keys"], [["count", "status"]])
        argument = surface["cli/automa_cli/app.py::cli-argument:verbose"]
        self.assertEqual(argument["flags"], ["--verbose", "-v"])
        self.assertIn("cli/automa_cli/app.py::cli-command:run", surface)
        self.assertTrue(all("line" not in descriptor for descriptor in surface.values()))

    def test_surface_descriptor_changes_for_signature_without_line_drift(self) -> None:
        before = analyze_coupling({"app.py": "def run(value, *, dry=False):\n    return {'ok': value}\n"})
        after = analyze_coupling({"app.py": "\n\n\ndef run(value, *, dry=False):\n    return {'ok': value}\n"})
        key = "app.py::run"
        self.assertEqual(before["contracts"]["surface"][key], after["contracts"]["surface"][key])
        changed = analyze_coupling(
            {"app.py": "def run(value, extra=1, *, dry=True):\n    return {'ok': value}\n"}
        )
        self.assertNotEqual(before["contracts"]["surface"][key], changed["contracts"]["surface"][key])


if __name__ == "__main__":
    unittest.main()
