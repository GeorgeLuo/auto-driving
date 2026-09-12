"""Independent control-flow and error-handling pattern indicators."""

from __future__ import annotations

import ast
from collections.abc import Mapping
from typing import Any

from .context import AnalysisContext
from .evidence import LocatedNode
from .report import kind_finding, measured_factor as _factor, source_code as _source_code


FACTOR = "patterns"
INDICATOR_ID = "structure.patterns"
INDICATOR_VERSION = "1"
DETECTOR_IDS = {
    "redundant_any_guard": f"{INDICATOR_ID}.redundant_any_guard",
    "raise": f"{INDICATOR_ID}.raise",
    "logged_error": f"{INDICATOR_ID}.logged_error",
    "bare_except": f"{INDICATOR_ID}.bare_except",
    "broad_except": f"{INDICATOR_ID}.broad_except",
    "swallowed_exception": f"{INDICATOR_ID}.swallowed_exception",
}


def analyze(context: AnalysisContext) -> dict[str, Any]:
    """Measure recognized error and control-flow patterns."""

    trees = {
        path: tree
        for path, tree in context.trees(family="default").items()
        if path.endswith(".py")
    }
    return _analyze_trees(trees, context=context)


def _analyze_trees(
    trees: Mapping[str, ast.Module], *, context: AnalysisContext | None = None
) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    bare_except_count = 0
    broad_except_count = 0
    raise_count = 0
    logged_error_count = 0
    swallowed_exception_count = 0
    redundant_any_guard_count = 0

    for path in sorted(trees):
        for node in ast.walk(trees[path]):
            if any_guard(node):
                redundant_any_guard_count += 1
                guard, aggregate = _any_guard_parts(node)
                findings.append(
                    _finding(
                        context,
                        {
                            "path": path,
                            "line": int(node.lineno),
                            "kind": "redundant_any_guard",
                            "expression": ast.unparse(node),
                            "message": (
                                "bool(container) guards any() over the same container. "
                                "For ordinary containers any() already handles emptiness; "
                                "inspect builtin shadowing and custom truth/iteration effects before simplifying."
                            ),
                        },
                        path=path,
                        node=node,
                        related_nodes=(
                            LocatedNode(path, guard),
                            LocatedNode(path, aggregate),
                        ),
                        pattern={
                            "matched": {
                                "bool": _source_code(context, path, guard),
                                "any": _source_code(context, path, aggregate),
                            }
                        },
                        inspection_question=(
                            "Inspect builtin shadowing and custom truth or iteration effects before simplifying."
                        ),
                    )
                )
            if isinstance(node, ast.Raise):
                raise_count += 1
                findings.append(
                    _finding(
                        context,
                        {
                            "path": path,
                            "line": int(node.lineno),
                            "kind": "raise",
                            "message": "Raise statement observed.",
                        },
                        path=path,
                        node=node,
                        inspection_question="Inspect whether this raise is part of the intended boundary contract.",
                    )
                )
            elif isinstance(node, ast.Call) and is_logged_error(node):
                logged_error_count += 1
                findings.append(
                    _finding(
                        context,
                        {
                            "path": path,
                            "line": int(node.lineno),
                            "kind": "logged_error",
                            "message": "Recognized error-logging call observed.",
                        },
                        path=path,
                        node=node,
                        inspection_question="Inspect whether this error log preserves the owning boundary's recovery contract.",
                    )
                )
            elif isinstance(node, ast.ExceptHandler):
                if node.type is None:
                    bare_except_count += 1
                    findings.append(
                        _finding(
                            context,
                            {
                                "path": path,
                                "line": int(node.lineno),
                                "kind": "bare_except",
                                "message": "Bare except clause observed.",
                            },
                            path=path,
                            node=node,
                            inspection_question="Inspect whether catching every exception is intentional at this boundary.",
                        )
                    )
                elif is_broad_except(node.type):
                    broad_except_count += 1
                    findings.append(
                        _finding(
                            context,
                            {
                                "path": path,
                                "line": int(node.lineno),
                                "kind": "broad_except",
                                "message": "Broad except for Exception or BaseException observed.",
                            },
                            path=path,
                            node=node,
                            inspection_question="Inspect whether this broad exception boundary is intentional.",
                        )
                    )
                if is_pass_only(node.body):
                    swallowed_exception_count += 1
                    findings.append(
                        _finding(
                            context,
                            {
                                "path": path,
                                "line": int(node.lineno),
                                "kind": "swallowed_exception",
                                "message": "Except body is only pass; exception may be swallowed.",
                            },
                            path=path,
                            node=node,
                            inspection_question="Inspect whether swallowing this exception is intentional and observable.",
                        )
                    )

    return _factor(
        {
            "bare_except_count": bare_except_count,
            "broad_except_count": broad_except_count,
            "raise_count": raise_count,
            "logged_error_count": logged_error_count,
            "swallowed_exception_count": swallowed_exception_count,
            "redundant_any_guard_count": redundant_any_guard_count,
        },
        findings,
        [
            "Inspect intent at the owning boundary; recognized patterns are not a style grade.",
            "Redundant-any guards match syntax only: builtin bool/any and ordinary container semantics are not proven. Aliases, attribute guards, and arbitrary iterator factories are not followed.",
        ],
    )


def any_guard(node: ast.AST) -> bool:
    """Recognize an explicit emptiness guard over the same ``any`` input."""

    return _any_guard_parts(node) is not None


def _any_guard_parts(node: ast.AST) -> tuple[ast.Call, ast.Call] | None:
    if (
        not isinstance(node, ast.BoolOp)
        or not isinstance(node.op, ast.And)
        or len(node.values) != 2
    ):
        return None
    guard, aggregate = node.values
    if not (_unary_named_call(guard, "bool") and _unary_named_call(aggregate, "any")):
        return None
    container = guard.args[0]
    if not isinstance(container, ast.Name):
        return None
    iterable = aggregate.args[0]
    if isinstance(iterable, ast.GeneratorExp):
        if len(iterable.generators) != 1 or iterable.generators[0].is_async:
            return None
        iterable = iterable.generators[0].iter
    if isinstance(iterable, ast.Call):
        if iterable.args or iterable.keywords or not isinstance(iterable.func, ast.Attribute):
            return None
        if iterable.func.attr not in {"values", "keys", "items"}:
            return None
        iterable = iterable.func.value
    if not (isinstance(iterable, ast.Name) and iterable.id == container.id):
        return None
    return guard, aggregate


def redundant_any_guard(node: ast.AST) -> bool:
    """Alias retaining the detector's descriptive name for direct tests."""

    return any_guard(node)


def is_broad_except(node: ast.AST) -> bool:
    if isinstance(node, ast.Name) and node.id in {"Exception", "BaseException"}:
        return True
    if isinstance(node, ast.Tuple):
        return any(is_broad_except(elt) for elt in node.elts)
    return False


def is_pass_only(body: list[ast.stmt]) -> bool:
    return len(body) == 1 and isinstance(body[0], ast.Pass)


def is_logged_error(node: ast.Call) -> bool:
    func = node.func
    if not isinstance(func, ast.Attribute) or not isinstance(func.value, ast.Name):
        return False
    if func.value.id == "logging" and func.attr in {"exception", "error"}:
        return True
    return func.value.id == "logger" and func.attr == "exception"


def _unary_named_call(node: ast.AST, name: str) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == name
        and len(node.args) == 1
        and not node.keywords
    )


def _finding(
    context: AnalysisContext | None,
    finding: dict[str, Any],
    *,
    path: str,
    node: ast.AST,
    related_nodes: tuple[LocatedNode, ...] = (),
    pattern: Mapping[str, Any] | None = None,
    uncertainty: list[str] | None = None,
    inspection_question: str | None = None,
) -> dict[str, Any]:
    return kind_finding(
        context,
        finding,
        detector_ids=DETECTOR_IDS,
        indicator_version=INDICATOR_VERSION,
        path=path,
        node=node,
        related_nodes=related_nodes,
        pattern=pattern,
        uncertainty=uncertainty,
        inspection_question=(
            inspection_question
            or "Inspect the owning boundary before drawing a conclusion from this static candidate."
        ),
    )


__all__ = [
    "DETECTOR_IDS",
    "FACTOR",
    "INDICATOR_ID",
    "INDICATOR_VERSION",
    "analyze",
    "any_guard",
    "is_broad_except",
    "is_logged_error",
    "is_pass_only",
    "redundant_any_guard",
]
