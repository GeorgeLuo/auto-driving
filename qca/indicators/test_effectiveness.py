"""Independent static test-effectiveness indicators.

The legacy verification factor is intentionally conservative: each detector
reports a review candidate and never decides that a test is ineffective.  The
public :func:`analyze` entry point consumes the shared verification analysis
context, while the small detector predicates below can be exercised directly
by indicator tests or a future registry.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Mapping, Sequence
from typing import Any

from .context import AnalysisContext
from .evidence import LocatedNode, enrich_finding
from .report import (
    call_name as _call_name,
    source_expression as _source_expression,
    verification_inputs as _verification_inputs,
)


FACTOR = "test_effectiveness"
INDICATOR_ID = "verification.test_effectiveness"
INDICATOR_VERSION = "1"

DETECTOR_IDS = {
    "literal_assertion": f"{INDICATOR_ID}.literal_assertion",
    "tautological_assertion": f"{INDICATOR_ID}.tautological_assertion",
    "assignment_readback": f"{INDICATOR_ID}.assignment_readback",
    "string_expected_assertion": f"{INDICATOR_ID}.string_expected_assertion",
    "formatted_literal_assertion": f"{INDICATOR_ID}.formatted_literal_assertion",
    "private_import": f"{INDICATOR_ID}.private_import",
    "private_helper_call": f"{INDICATOR_ID}.private_helper_call",
}

_TEST_FILE_RE = re.compile(
    r"(?:^|/)(?:tests?|spec)(?:/|$)|(?:^|/)(?:test_[^/]+|[^/]+_test)\.py$"
)
_ASSERT_EQUAL_METHODS = {
    "assertEqual",
    "assertAlmostEqual",
    "assertSequenceEqual",
    "assertListEqual",
    "assertTupleEqual",
    "assertSetEqual",
    "assertDictEqual",
    "assertCountEqual",
    "assertIs",
}
_ASSERT_LITERAL_METHODS = {
    "assertTrue",
    "assertFalse",
    "assertIsNone",
    "assertIsNotNone",
}
_STRING_EXPECTED_METHODS = {
    "assertEqual",
    "assertNotEqual",
    "assertMultiLineEqual",
    "assertIn",
    "assertNotIn",
    "assertRegex",
    "assertNotRegex",
}
_FORMATTED_LITERAL_RE = re.compile(r"[|<>\n`]|\d+(?:\.\d+)?%")
_FORMATTED_LITERAL_MIN_LENGTH = 40


def analyze(context: AnalysisContext) -> dict[str, Any]:
    """Return the legacy ``test_effectiveness`` factor for ``context``.

    ``context`` is :class:`qca.indicators.context.AnalysisContext`.  The
    detector predicates below remain independently callable with AST nodes;
    aggregation uses the shared context so every indicator sees the same
    parsed verification family.
    """

    ordered_sources, parsed, parse_errors, python_file_count = _verification_inputs(context)
    return _test_effectiveness_factor(
        ordered_sources,
        parsed,
        parse_errors,
        python_file_count,
        context=context,
    )


def literal_assertion(node: ast.AST) -> bool:
    """Return whether an assertion is a literal-only candidate."""

    if isinstance(node, ast.Assert):
        return _is_literal_expression(node.test)
    if not isinstance(node, ast.Call):
        return False
    name = _call_name(node.func)
    return bool(name in _ASSERT_LITERAL_METHODS and node.args and _is_literal_expression(node.args[0]))


def tautological_assertion(node: ast.AST) -> bool:
    """Return whether both sides of a supported equality assertion match."""

    if isinstance(node, ast.Assert):
        return _is_same_operand_compare(node.test)
    if not isinstance(node, ast.Call):
        return False
    name = _call_name(node.func)
    return bool(
        name in _ASSERT_EQUAL_METHODS
        and len(node.args) >= 2
        and _ast_equal(node.args[0], node.args[1])
    )


def assignment_readback(
    node: ast.AST,
    assignments: Mapping[ast.AST, ast.AST] | None = None,
) -> bool:
    """Return whether ``node`` has a directly assigned literal readback.

    ``assignments`` is the map returned by :func:`assignment_readbacks`.  A
    map is required because this detector deliberately does not infer aliases,
    branches, constructors, or interprocedural state.
    """

    return assignments is not None and node in assignments


def string_expected_assertion(node: ast.AST) -> bool:
    """Return whether a supported assertion compares against a string."""

    return bool(_string_constants_in_assertion(node))


def formatted_literal_assertion(node: ast.AST) -> bool:
    """Return whether a supported assertion compares against a formatted literal."""

    constants = _string_constants_in_assertion(node)
    return bool(constants and _is_formatted_literal(max(constants, key=len)))


def private_import(node: ast.AST) -> bool:
    """Return whether an import contains a private production name."""

    return isinstance(node, (ast.Import, ast.ImportFrom)) and bool(_private_imported_names(node))


def private_helper_call(node: ast.AST, local_private: set[str] | None = None) -> bool:
    """Return whether a call targets a private non-local helper."""

    return isinstance(node, ast.Call) and _is_private_helper_call(node, local_private or set())


def candidate_kinds(
    node: ast.AST,
    assignments: Mapping[ast.AST, ast.AST] | None = None,
) -> tuple[str, ...]:
    """Return every matching assertion candidate, in detector order.

    This deliberately retains lower-priority matches that the legacy
    aggregate suppresses.  For example, an assignment readback can also be a
    string-expected assertion; callers can inspect both before choosing the
    legacy precedence through :func:`assertion_candidate_kind`.
    """

    matches: list[str] = []
    if literal_assertion(node):
        matches.append("literal_assertion")
    if tautological_assertion(node):
        matches.append("tautological_assertion")
    if assignment_readback(node, assignments):
        matches.append("assignment_readback")
    if string_expected_assertion(node):
        matches.append("string_expected_assertion")
        if formatted_literal_assertion(node):
            matches.append("formatted_literal_assertion")
    return tuple(matches)


def assertion_candidate_kind(
    node: ast.AST,
    assignments: Mapping[ast.AST, ast.AST] | None = None,
) -> str | None:
    """Return the legacy first-match candidate kind for ``node``."""

    if literal_assertion(node):
        return "literal_assertion"
    if tautological_assertion(node):
        return "tautological_assertion"
    if assignment_readback(node, assignments):
        return "assignment_readback"
    if formatted_literal_assertion(node):
        return "formatted_literal_assertion"
    if string_expected_assertion(node):
        return "string_expected_assertion"
    return None


def assignment_readbacks(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    """Locate literal readbacks in straight-line ``test_*`` bodies."""

    matches: dict[ast.AST, ast.AST] = {}
    for function in ast.walk(tree):
        if not (
            isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
            and function.name.startswith("test_")
        ):
            continue
        assigned: dict[str, tuple[ast.AST, ast.AST]] = {}
        for statement in function.body:
            target = value = None
            if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
                target, value = statement.targets[0], statement.value
            elif isinstance(statement, ast.AnnAssign):
                target, value = statement.target, statement.value
            if target is not None:
                key = _readback_target(target)
                literal = isinstance(value, ast.Constant) or (
                    isinstance(value, ast.UnaryOp)
                    and isinstance(value.op, (ast.UAdd, ast.USub))
                    and isinstance(value.operand, ast.Constant)
                    and type(value.operand.value) in (int, float, complex)
                )
                if key is None or not literal or any(isinstance(n, ast.Call) for n in ast.walk(statement)):
                    assigned.clear()
                    continue
                if isinstance(target, ast.Attribute):
                    assigned.clear()
                else:
                    assigned = {k: v for k, v in assigned.items() if "." not in k and k != key}
                assigned[key] = (value, statement)
                continue

            assertion = statement.value if isinstance(statement, ast.Expr) else statement
            operands = None
            if isinstance(assertion, ast.Assert):
                comparison = assertion.test
                if (
                    isinstance(comparison, ast.Compare)
                    and len(comparison.ops) == 1
                    and isinstance(comparison.ops[0], (ast.Eq, ast.Is))
                ):
                    operands = (comparison.left, comparison.comparators[0])
            elif (
                isinstance(assertion, ast.Call)
                and _call_name(assertion.func) in {"assertEqual", "assertIs"}
                and len(assertion.args) == 2
                and not assertion.keywords
            ):
                operands = tuple(assertion.args)
            nested_calls = any(
                isinstance(n, (ast.Call, ast.Await, ast.Yield, ast.NamedExpr))
                and n is not assertion
                for n in ast.walk(statement)
            )
            if operands is not None and not nested_calls:
                for actual, expected in (operands, operands[::-1]):
                    prior = assigned.get(_readback_target(actual))
                    if prior is not None and _ast_equal(prior[0], expected):
                        matches[assertion] = prior[1]
                        break
            assigned.clear()
    return matches


def _test_effectiveness_factor(
    ordered_sources: list[tuple[str, str]],
    parsed: list[tuple[str, str, ast.AST]],
    parse_errors: list[dict[str, Any]],
    python_file_count: int,
    *,
    context: AnalysisContext | None = None,
) -> dict[str, Any]:
    test_paths = {path for path, _ in ordered_sources if _is_test_source(path)}
    candidate_sites: list[dict[str, Any]] = []
    assertion_count = 0
    literal_count = 0
    tautological_count = 0
    readback_count = 0
    string_expected_count = 0
    formatted_literal_count = 0
    private_import_count = 0
    private_helper_call_count = 0
    private_import_files: set[str] = set()
    test_case_count = 0
    test_parse_error_count = sum(1 for error in parse_errors if error["path"] in test_paths)

    for path, text, tree in parsed:
        module_is_test = path in test_paths
        parents = _parent_map(tree)
        local_private = _private_names_defined(tree)
        readbacks = assignment_readbacks(tree)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                test_case_count += 1
            if module_is_test and isinstance(node, (ast.Import, ast.ImportFrom)):
                imported = _private_imported_names(node)
                if imported:
                    private_import_count += len(imported)
                    private_import_files.add(path)
                    _append_test_candidate(
                        candidate_sites,
                        context=context,
                        kind="private_import",
                        path=path,
                        node=node,
                        text=text,
                        reason=(
                            "Private production import in a test; inspect whether the test can use a public entrypoint."
                        ),
                    )
                continue
            if not isinstance(node, (ast.Assert, ast.Call)):
                continue
            if not module_is_test and not _inside_test_callable(node, parents):
                continue
            if isinstance(node, ast.Call) and private_helper_call(node, local_private):
                private_helper_call_count += 1
                _append_test_candidate(
                    candidate_sites,
                    context=context,
                    kind="private_helper_call",
                    path=path,
                    node=node,
                    text=text,
                    reason=(
                        "Private production helper called from a test; inspect whether a public entrypoint covers the behavior."
                    ),
                )
            candidate_kind = assertion_candidate_kind(node, readbacks)
            if isinstance(node, ast.Assert):
                assertion_count += 1
            elif _is_assertion_call(node):
                assertion_count += 1
            if candidate_kind is None:
                continue
            if candidate_kind == "literal_assertion":
                literal_count += 1
            elif candidate_kind == "tautological_assertion":
                tautological_count += 1
            elif candidate_kind == "assignment_readback":
                readback_count += 1
            elif candidate_kind == "formatted_literal_assertion":
                formatted_literal_count += 1
                string_expected_count += 1
            else:
                string_expected_count += 1
            _append_test_candidate(
                candidate_sites,
                context=context,
                kind=candidate_kind,
                path=path,
                node=node,
                text=text,
                reason=(
                    "Static candidate only; inspect assertion intent and its input variation before drawing a conclusion."
                ),
            )

            if candidate_kind == "assignment_readback":
                assignment = readbacks[node]
                candidate_sites[-1]["assignment"] = {
                    "path": path,
                    "line": assignment.lineno,
                    "column": assignment.col_offset + 1,
                    "expression": _source_expression(text, assignment),
                }
                candidate_sites[-1]["reason"] = candidate_sites[-1]["message"] = (
                    "Assertion reads back a directly assigned literal; inspect whether a meaningful "
                    "boundary is exercised. Attribute access may invoke a setter or getter."
                )
                # The assertion and assignment are both exact detector nodes;
                # the context may render the latter as related code evidence.
                candidate_sites[-1] = _enrich_finding(
                    context,
                    candidate_sites[-1],
                    path=path,
                    node=node,
                    related_nodes=(LocatedNode(path=path, node=assignment, family="verification"),),
                    candidate_kind=candidate_kind,
                )

    candidate_count = literal_count + tautological_count + readback_count + string_expected_count
    limitations = [
        "Literal, same-operand, string-expected, and formatted-literal assertions are candidates for review, not a judgment that a test is ineffective.",
        "Private production imports and helper calls are candidates; same-module test helpers are omitted; numeric expected values alone are not candidates.",
        "Assignment readbacks track scalar literals in straight-line test function bodies only; calls, control flow, and uncertain mutations end tracking. No alias, constructor, subscript, or interprocedural inference is performed; attribute access may exercise meaningful descriptors.",
        "Static inspection does not infer input variation, mocks, explicit state setup, or the behavior under test.",
    ]
    if test_parse_error_count:
        limitations.append(
            f"{test_parse_error_count} test-like Python file(s) could not be parsed and were excluded from assertion counts."
        )
    metrics = {
        "source_file_count": len(ordered_sources),
        "python_file_count": python_file_count,
        "test_file_count": len(test_paths),
        "test_case_count": test_case_count,
        "assertion_count": assertion_count,
        "literal_assertion_candidates": literal_count,
        "tautological_assertion_candidates": tautological_count,
        "assignment_readback_candidates": readback_count,
        "string_expected_assertion_count": string_expected_count,
        "formatted_literal_assertion_count": formatted_literal_count,
        "private_import_count": private_import_count,
        "private_import_test_file_count": len(private_import_files),
        "private_helper_call_count": private_helper_call_count,
        "candidate_assertion_count": candidate_count,
        "candidate_site_count": len(candidate_sites),
        "parse_error_count": test_parse_error_count,
    }
    return {
        "status": "measured",
        "metrics": metrics,
        "findings": candidate_sites,
        "details": {"candidate_sites_are_complete": True},
        "limitations": limitations,
    }


def _append_test_candidate(
    sites: list[dict[str, Any]],
    *,
    context: AnalysisContext | None,
    kind: str,
    path: str,
    node: ast.AST,
    text: str,
    reason: str,
) -> None:
    finding = {
        "kind": kind,
        "path": path,
        "line": int(getattr(node, "lineno", 0) or 0),
        "column": int((getattr(node, "col_offset", 0) or 0) + 1),
        "expression": _source_expression(text, node),
        "reason": reason,
        "message": reason,
    }
    sites.append(
        _enrich_finding(
            context,
            finding,
            path=path,
            node=node,
            candidate_kind=kind,
        )
    )


def _is_test_source(path: str) -> bool:
    normalized = path.replace("\\", "/").strip("/")
    return bool(_TEST_FILE_RE.search(normalized))


def _parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    return parents


def _inside_test_callable(node: ast.AST, parents: Mapping[ast.AST, ast.AST]) -> bool:
    current = parents.get(node)
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)) and current.name.startswith("test_"):
            return True
        current = parents.get(current)
    return False


def _is_assertion_call(node: ast.Call) -> bool:
    name = _call_name(node.func)
    return bool(name and (name.startswith("assert") or name in {"fail", "failUnless", "failIf"}))


def _is_private_identifier(name: str) -> bool:
    return name.startswith("_") and not name.startswith("__")


def _private_names_defined(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if _is_private_identifier(node.name):
                names.add(node.name)
    return names


def _is_test_module_name(module: str | None) -> bool:
    if not module:
        return False
    return any(part in {"tests", "test", "testing"} or part.startswith("test_") for part in module.split("."))


def _private_imported_names(node: ast.Import | ast.ImportFrom) -> tuple[str, ...]:
    if isinstance(node, ast.Import):
        return tuple(alias.name.split(".")[-1] for alias in node.names if _is_private_identifier(alias.name.split(".")[-1]))
    if node.module == "__future__" or (node.level or 0) > 0 or _is_test_module_name(node.module):
        return ()
    return tuple(alias.name for alias in node.names if _is_private_identifier(alias.name))


def _is_private_helper_call(node: ast.Call, local_private: set[str]) -> bool:
    name = _call_name(node.func)
    return bool(name and _is_private_identifier(name) and name not in local_private)


def _constant_text(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Constant) or not isinstance(node.value, (str, bytes)):
        return None
    if isinstance(node.value, bytes):
        return node.value.decode("utf-8", "replace")
    return node.value


def _is_formatted_literal(text: str) -> bool:
    return len(text) >= _FORMATTED_LITERAL_MIN_LENGTH or bool(_FORMATTED_LITERAL_RE.search(text))


def _string_constants_in_assertion(node: ast.AST) -> list[str]:
    values: list[str] = []
    if isinstance(node, ast.Assert) and isinstance(node.test, ast.Compare):
        for part in (node.test.left, *node.test.comparators):
            text = _constant_text(part)
            if text is not None:
                values.append(text)
        return values
    if not isinstance(node, ast.Call):
        return values
    name = _call_name(node.func)
    if name not in _STRING_EXPECTED_METHODS:
        return values
    for arg in node.args[:2]:
        text = _constant_text(arg)
        if text is not None:
            values.append(text)
    return values


def _readback_target(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return f"{node.value.id}.{node.attr}"
    return None


def _is_literal_expression(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.Not, ast.UAdd, ast.USub)):
        return isinstance(node.operand, ast.Constant)
    return False


def _is_same_operand_compare(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Compare)
        and len(node.ops) == 1
        and len(node.comparators) == 1
        and isinstance(node.ops[0], (ast.Eq, ast.Is))
        and _ast_equal(node.left, node.comparators[0])
    )


def _ast_equal(left: ast.AST, right: ast.AST) -> bool:
    return ast.dump(left, annotate_fields=True, include_attributes=False) == ast.dump(
        right, annotate_fields=True, include_attributes=False
    )


def _enrich_finding(
    context: AnalysisContext | None,
    finding: dict[str, Any],
    *,
    path: str,
    node: ast.AST,
    candidate_kind: str,
    related_nodes: Sequence[LocatedNode] = (),
) -> dict[str, Any]:
    """Attach stable identity and exact source evidence when context permits."""

    finding = dict(finding)
    if not isinstance(context, AnalysisContext):
        return finding
    return enrich_finding(
        finding,
        context=context,
        indicator_id=DETECTOR_IDS[candidate_kind],
        indicator_version=INDICATOR_VERSION,
        path=path,
        node=node,
        family="verification",
        related_nodes=related_nodes,
        pattern={"candidate_kind": candidate_kind},
        uncertainty=["static_candidate"],
        inspection_question=(
            "Inspect assertion intent and input variation before drawing a conclusion."
        ),
    )


__all__ = [
    "DETECTOR_IDS",
    "FACTOR",
    "INDICATOR_ID",
    "INDICATOR_VERSION",
    "analyze",
    "assignment_readback",
    "assignment_readbacks",
    "assertion_candidate_kind",
    "candidate_kinds",
    "formatted_literal_assertion",
    "literal_assertion",
    "private_helper_call",
    "private_import",
    "string_expected_assertion",
    "tautological_assertion",
]
