from __future__ import annotations

import ast
import unittest

from qca.indicators import INDICATOR_REGISTRY, opaque_conditionals
from qca.indicators.context import AnalysisContext


def inspect(expression: str, *, discarded: bool = False):
    return opaque_conditionals.inspect_condition(ast.parse(expression, mode="eval").body, discarded=discarded)


class OpaqueConditionalTests(unittest.TestCase):
    def test_counts_predicates_across_parentheses_negation_and_comparison_chains(self):
        for expression, count in (
            ("a", 1), ("-a", 1), ("not a", 1), ("a and b", 2),
            ("a and (b and c)", 3), ("not (a and b and c)", 3),
            ("0 < x < 10", 2), ("0 < x < y < 10", 3),
        ):
            with self.subTest(expression=expression):
                result = inspect(expression)
                self.assertEqual(result["predicate_count"], count)
                self.assertEqual("too_many_predicates" in result["reasons"], count > 2)

    def test_reports_mixed_operators_once_even_with_explicit_parentheses(self):
        context = AnalysisContext.from_sources({"app.py": "if a or (b and c):\n    pass\n"})
        result = opaque_conditionals.analyze(context)
        self.assertEqual(result["metrics"]["opaque_conditional_count"], 1)
        self.assertEqual(result["findings"][0]["reasons"], ["too_many_predicates", "mixed_boolean_operators"])

    def test_reuse_tracks_full_subjects_and_ignores_callees_and_common_prefixes(self):
        for expression, repeated in (
            ("x and x", ["x"]),
            ("x or x", ["x"]),
            ("x > 0 and x < 10", ["x"]),
            ("self.mode == 'run' or self.mode == 'stop'", ["self.mode"]),
            ("host['mode'] == 'run' or host['mode'] == 'stop'", ["host['mode']"]),
            ("host.get('mode') == 'run' or host.get('state') == 'stop'", ["host"]),
            ("self.left == 1 and self.right == 2", []),
            ("host['left'] == 1 and host['right'] == 2", []),
            ("valid(a) and valid(b)", []),
            ("isinstance(a, str) and isinstance(b, str)", []),
        ):
            with self.subTest(expression=expression):
                self.assertEqual(inspect(expression)["repeated_subjects"], repeated)

    def test_exempts_accepting_and_rejecting_two_part_safety_guards(self):
        for expression in (
            "x is not None and x.ready", "None is not x and x.ready",
            "isinstance(x, Thing) and x.ready", "x and x.ready", "bool(x) and x.ready",
            "isinstance(x, str) and x", "not isinstance(x, str) or not x",
            "x is None or not x.ready", "not isinstance(x, Thing) or not x.ready",
            "not x or x.ready", "not bool(x) or x.ready",
            "self.item is not None and self.item.ready",
            "host['item'] is not None and host['item'].ready",
        ):
            with self.subTest(expression=expression):
                self.assertEqual(inspect(expression)["reasons"], [])
        # Guards do not exempt an arbitrary comparison, a longer check, or effects.
        self.assertIn("repeated_subjects", inspect("x > 0 and x < 10")["reasons"])
        self.assertIn("too_many_predicates", inspect("isinstance(x, str) and x and x != 'manual'")["reasons"])
        self.assertIn("execution_in_evaluation", inspect("x and x.pop()")["reasons"])

    def test_recognizes_execution_without_claiming_arbitrary_calls_have_effects(self):
        for expression, effects in (
            ("(x := read())", ["assignment_expression"]),
            ("await ready()", ["await_or_yield"]),
            ("(yield value)", ["await_or_yield"]),
            ("(yield from values)", ["await_or_yield"]),
            ("items.pop()", ["mutating_call"]),
            ("ready and items.append(value)", ["mutating_call"]),
            ("isinstance(x, str) and valid(x)", []),
            ("ready and predicate()", []),
            ("lambda: items.pop()", []),
            ("any(items.pop() for item in values)", []),
        ):
            with self.subTest(expression=expression):
                self.assertEqual(inspect(expression)["execution_kinds"], effects)
        self.assertEqual(inspect("ready and act()", discarded=True)["execution_kinds"], ["short_circuit_action"])

    def test_checks_all_condition_contexts_and_boolean_values_without_nested_duplicates(self):
        source = (
            "if a and b and c:\n    pass\n"
            "while a and b and c:\n    break\n"
            "value = 1 if a and b and c else 0\n"
            "assert a and b and c\n"
            "values = [x for x in xs if a and b and c]\n"
            "value = a and (b and c)\n"
            "ready and act()\n"
            "if (item := read()):\n    pass\n"
        )
        result = opaque_conditionals.analyze(AnalysisContext.from_sources({"app.py": source}))
        self.assertEqual(result["metrics"]["opaque_conditional_count"], 8)
        self.assertEqual(result["metrics"]["opaque_conditional_too_many_predicates_count"], 6)
        self.assertEqual(result["metrics"]["opaque_conditional_execution_in_evaluation_count"], 2)

    def test_multiline_evidence_and_registered_identity_use_the_exact_expression(self):
        expression = "é.ready\n    and é.valid\n    and é.running"
        source = "if (" + expression + "):\n    pass\n"
        context = AnalysisContext.from_sources({"app.py": source}, revision="head-sha")
        finding = opaque_conditionals.analyze(context)["findings"][0]
        self.assertIn(finding["indicator"]["id"], INDICATOR_REGISTRY)
        self.assertEqual(finding["evidence"]["revision"], "head-sha")
        primary = finding["evidence"]["primary"]
        self.assertEqual(primary["code"], expression)
        self.assertEqual(primary["range"]["start"]["line"], 1)
        self.assertEqual(primary["range"]["end"]["line"], 3)

    def test_remote_create_host_manual_examples_are_captured(self):
        # Manually inspected at b8b910c against merge base ce0e3a87: six
        # introduced sites; host_status.py:127 moved from vehicles.py:517.
        # Keep these independent of the detector so changing its rules cannot
        # silently redefine the smoke-test positives.
        for location, expression, count, reasons in (
            ("host_status.py:81", """isinstance(client_info, dict)
                and client_info.get("kind") == "host"
                and client_info.get("host_name") == host["host_name"]
                and isinstance(client_info.get("url"), str)
                and client_info["url"].rstrip("/") == host["url"]""", 5,
             ["too_many_predicates", "repeated_subjects"]),
            ("host_status.py:127", 'isinstance(mode, str) and mode and mode != "manual"', 3,
             ["too_many_predicates", "repeated_subjects"]),
            ("step_activations.py:217", """not isinstance(configuration, dict) or configuration.get("schema") != "automa_host_configuration"
                or configuration.get("host_name") != host_name
                or configuration.get("provider") not in HOST_PROVIDERS
                or not isinstance(configuration.get("connection"), dict)""", 5,
             ["too_many_predicates", "repeated_subjects"]),
            ("step_activations.py:208,234; deploy.py:517",
             'host_name in {".", ".."} or safe_path_part(host_name) != host_name or not host_name', 3,
             ["too_many_predicates", "repeated_subjects"]),
            ("vehicles.py:303", 'entry in listed or (provider == "chase-sim" and url == default_chase_url())', 3,
             ["too_many_predicates", "mixed_boolean_operators"]),
        ):
            with self.subTest(location=location):
                result = inspect("(" + expression + ")")
                self.assertEqual(result["predicate_count"], count)
                self.assertEqual(result["reasons"], reasons)


if __name__ == "__main__":
    unittest.main()
