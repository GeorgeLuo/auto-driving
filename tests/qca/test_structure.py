from __future__ import annotations

import ast
import unittest

from qca import analyze_sources, report_to_dict
from qca.factors.structure import analyze_structure


class StructureFactorTests(unittest.TestCase):
    def test_mutable_default_and_append_produce_functional_style_findings(self) -> None:
        result = analyze_structure(
            {
                "app.py": (
                    "def run(value, items=[]):\n"
                    "    items.append(value)\n"
                    "    return items\n"
                ),
            }
        )
        factor = result["functional_style"]
        kinds = {item["kind"] for item in factor["findings"]}
        self.assertIn("mutable_default", kinds)
        self.assertIn("mutating_call", kinds)
        self.assertGreaterEqual(factor["metrics"]["mutable_default_count"], 1)
        self.assertGreaterEqual(factor["metrics"]["mutating_call_count"], 1)
        self.assertGreaterEqual(factor["metrics"]["recognized_effect_count"], 2)
        self.assertTrue(
            any("purity" in item.lower() for item in factor["limitations"])
        )

    def test_identical_callables_form_redundancy_clone_group(self) -> None:
        result = analyze_structure(
            {
                "a.py": (
                    "def left(value):\n"
                    "    total = value + 1\n"
                    "    return total * 2\n"
                ),
                "b.py": (
                    "def right(amount):\n"
                    "    total = amount + 1\n"
                    "    return total * 2\n"
                ),
            }
        )
        redundancy = result["redundancy"]
        clones = [item for item in redundancy["findings"] if item["kind"] == "callable_clone"]
        self.assertEqual(len(clones), 1)
        finding = clones[0]
        self.assertEqual(len(finding["occurrences"]), 2)
        self.assertEqual(
            {(item["path"], item["name"]) for item in finding["occurrences"]},
            {("a.py", "left"), ("b.py", "right")},
        )
        self.assertEqual(set(finding["paths"]), {"a.py", "b.py"})
        self.assertEqual(redundancy["metrics"]["clone_group_count"], 1)
        self.assertEqual(redundancy["metrics"]["cloned_callable_count"], 2)
        self.assertGreater(redundancy["metrics"]["duplicate_ast_loc"], 0)

    def test_stub_function_is_functionality_finding(self) -> None:
        result = analyze_structure({"hooks.py": "def hook():\n    pass\n"})
        functionality = result["functionality"]
        stubs = [item for item in functionality["findings"] if item["kind"] == "stub"]
        self.assertEqual(len(stubs), 1)
        self.assertEqual(stubs[0]["path"], "hooks.py")
        self.assertEqual(functionality["metrics"]["stub_count"], 1)

    def test_consumer_report_surfaces_redundant_any_guards(self) -> None:
        """Consumer: report the observed pattern at its source location."""
        expressions = [
            "bool(items) and any(items)",
            "bool(items) and any(check(x) for x in items)",
            "bool(items) and any(check(x) for x in items if x is not None)",
            "bool(record) and any(check(x) for x in record.values())",
            "bool(record) and any(record.keys())",
            "bool(record) and any(check(x) for x in record.items())",
        ]
        for expression in expressions:
            with self.subTest(expression=expression):
                source = f"def check_values(items, record):\n    return {expression}\n"
                factor = report_to_dict(analyze_sources({"app.py": source}))["factors"]["patterns"]
                self.assertEqual(factor["metrics"]["redundant_any_guard_count"], 1)
                finding = next(f for f in factor["findings"] if f["kind"] == "redundant_any_guard")
                self.assertEqual((finding["path"], finding["line"]), ("app.py", 2))
                self.assertEqual(
                    ast.dump(ast.parse(finding["expression"], mode="eval")),
                    ast.dump(ast.parse(expression, mode="eval")),
                )

    def test_boundary_any_guard_detector_preserves_distinct_conditions(self) -> None:
        """Boundary: do not suggest dropping checks with different semantics."""
        expressions = [
            "bool(items) and all(items)",
            "bool(items) and any(other)",
            "bool(items) or any(items)",
            "items is not None and any(items)",
            "len(items) > 1 and any(items)",
            "bool(items) and any(transform(items))",
            "bool(items) and any(x for x in other)",
            "bool(items) and any(x for x in items for y in other)",
            "bool(items) and any([check(x) for x in items])",
            "bool(obj.items) and any(obj.items)",
        ]
        for expression in expressions:
            with self.subTest(expression=expression):
                factor = analyze_sources({"app.py": f"result = {expression}\n"}).factors["patterns"]
                self.assertEqual(factor["metrics"]["redundant_any_guard_count"], 0)
                self.assertFalse(any(f["kind"] == "redundant_any_guard" for f in factor["findings"]))

    def test_boundary_guard_removal_preserves_container_results_and_predicate_calls(self) -> None:
        """Boundary: test the proposed simplification on ordinary containers."""
        for items in ([], [0], [0, 2, 3], (), (0, 2), set(), {0, 2}):
            before_calls, after_calls = [], []
            before = bool(items) and any(before_calls.append(x) or bool(x) for x in items)
            after = any(after_calls.append(x) or bool(x) for x in items)
            self.assertEqual((before, before_calls), (after, after_calls))
        for record in ({}, {"a": 0}, {"a": 0, "b": 2}):
            self.assertEqual(bool(record) and any(record.values()), any(record.values()))
        # Unlike any(), all() returns True on empty input: its guard matters.
        self.assertNotEqual(bool([]) and all([]), all([]))

    def test_bare_except_is_patterns_finding(self) -> None:
        result = analyze_structure(
            {
                "app.py": (
                    "def run():\n"
                    "    try:\n"
                    "        return 1\n"
                    "    except:\n"
                    "        return 0\n"
                ),
            }
        )
        patterns = result["patterns"]
        bare = [item for item in patterns["findings"] if item["kind"] == "bare_except"]
        self.assertEqual(len(bare), 1)
        self.assertEqual(patterns["metrics"]["bare_except_count"], 1)

    def test_syntax_error_does_not_crash_and_other_files_still_measured(self) -> None:
        result = analyze_structure(
            {
                "broken.py": "def oops(:\n",
                "ok.py": "def hook():\n    pass\n",
            }
        )
        self.assertEqual(
            set(result),
            {"redundancy", "patterns", "functional_style", "functionality"},
        )
        for factor in result.values():
            self.assertEqual(factor["status"], "measured")
            self.assertTrue(
                any(item["kind"] == "syntax_error" for item in factor["findings"])
            )
        stubs = [
            item
            for item in result["functionality"]["findings"]
            if item["kind"] == "stub"
        ]
        self.assertEqual(len(stubs), 1)
        self.assertEqual(stubs[0]["path"], "ok.py")

    def test_non_python_paths_are_ignored(self) -> None:
        result = analyze_structure(
            {
                "readme.md": "def run(items=[]):\n    items.append(1)\n",
                "data.json": '{"except": true}',
                "app.py": "def hook():\n    pass\n",
            }
        )
        for factor in result.values():
            paths = {item["path"] for item in factor["findings"]}
            self.assertNotIn("readme.md", paths)
            self.assertNotIn("data.json", paths)
        stubs = [
            item
            for item in result["functionality"]["findings"]
            if item["kind"] == "stub"
        ]
        self.assertEqual([item["path"] for item in stubs], ["app.py"])
        self.assertEqual(result["functional_style"]["metrics"]["mutable_default_count"], 0)

    def test_findings_always_have_message(self) -> None:
        result = analyze_structure(
            {
                "app.py": (
                    "def run(value, items=[]):\n"
                    "    items.append(value)\n"
                    "    try:\n"
                    "        raise ValueError(value)\n"
                    "    except:\n"
                    "        pass\n"
                    "    return items\n"
                    "    unused = 1\n"
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
                ),
            }
        )
        for name, factor in result.items():
            self.assertTrue(factor["limitations"], msg=name)
            for item in factor["findings"]:
                self.assertIn("path", item)
                self.assertIn("line", item)
                self.assertIn("kind", item)
                self.assertTrue(item["message"], msg=f"{name}:{item}")

    def test_analyze_structure_returns_exactly_four_keys(self) -> None:
        result = analyze_structure({"app.py": "VALUE = 1\n"})
        self.assertEqual(
            set(result),
            {"redundancy", "patterns", "functional_style", "functionality"},
        )
        for factor in result.values():
            self.assertEqual(factor["status"], "measured")
            self.assertIsInstance(factor["metrics"], dict)
            self.assertIsInstance(factor["findings"], list)
            self.assertTrue(factor["limitations"])


if __name__ == "__main__":
    unittest.main()
