"""Independent functional-style and effect indicators."""

from __future__ import annotations

import ast
from collections.abc import Mapping
from typing import Any

from ..factors._utils import _sort_findings
from .context import AnalysisContext
from .evidence import enrich_finding


FACTOR = "functional_style"
INDICATOR_ID = "structure.functional_style"
INDICATOR_VERSION = "1"
DETECTOR_IDS = {
    "mutable_default": f"{INDICATOR_ID}.mutable_default",
    "mutating_call": f"{INDICATOR_ID}.mutating_call",
    "open_call": f"{INDICATOR_ID}.open_call",
    "global_or_nonlocal": f"{INDICATOR_ID}.global_or_nonlocal",
    "attribute_write": f"{INDICATOR_ID}.attribute_write",
}
_MUTATING_METHODS = frozenset(
    {
        "append",
        "extend",
        "insert",
        "remove",
        "pop",
        "clear",
        "update",
        "add",
        "discard",
        "write",
        "close",
    }
)
_MUTABLE_CTORS = frozenset({"list", "dict", "set"})


def analyze(context: AnalysisContext) -> dict[str, Any]:
    """Measure recognized mutable state and side-effect syntax."""

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
    mutable_default_count = 0
    mutating_call_count = 0
    attribute_write_count = 0
    global_or_nonlocal_count = 0
    open_count = 0

    for path in sorted(trees):
        for node in ast.walk(trees[path]):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defaults = list(node.args.defaults)
                defaults.extend(
                    value for value in node.args.kw_defaults if value is not None
                )
                for default in defaults:
                    if mutable_default(default):
                        mutable_default_count += 1
                        findings.append(
                            _finding(
                                context,
                                {
                                    "path": path,
                                    "line": int(getattr(default, "lineno", node.lineno)),
                                    "kind": "mutable_default",
                                    "message": "Mutable default argument observed.",
                                },
                                ((path, default),),
                            )
                        )
            if isinstance(node, ast.Call) and mutating_call(node):
                mutating_call_count += 1
                findings.append(
                    _finding(
                        context,
                        {
                            "path": path,
                            "line": int(node.lineno),
                            "kind": "mutating_call",
                            "message": f"Mutating method call observed: {node.func.attr}.",
                        },
                        ((path, node),),
                    )
                )
            if isinstance(node, ast.Call) and open_call(node):
                open_count += 1
                findings.append(
                    _finding(
                        context,
                        {
                            "path": path,
                            "line": int(node.lineno),
                            "kind": "open_call",
                            "message": "open() call observed.",
                        },
                        ((path, node),),
                    )
                )
            if isinstance(node, (ast.Global, ast.Nonlocal)):
                global_or_nonlocal_count += 1
                findings.append(
                    _finding(
                        context,
                        {
                            "path": path,
                            "line": int(node.lineno),
                            "kind": "global_or_nonlocal",
                            "message": f"{type(node).__name__.lower()} statement observed.",
                        },
                        ((path, node),),
                    )
                )
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    attribute_write_count += _count_attribute_writes(
                        path, target, findings, int(node.lineno), context=context
                    )
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                attribute_write_count += _count_attribute_writes(
                    path, node.target, findings, int(node.lineno), context=context
                )
            elif isinstance(node, ast.AugAssign):
                attribute_write_count += _count_attribute_writes(
                    path, node.target, findings, int(node.lineno), context=context
                )

    recognized_effect_count = (
        mutable_default_count
        + mutating_call_count
        + attribute_write_count
        + global_or_nonlocal_count
        + open_count
    )
    return _factor(
        {
            "mutable_default_count": mutable_default_count,
            "mutating_call_count": mutating_call_count,
            "attribute_write_count": attribute_write_count,
            "global_or_nonlocal_count": global_or_nonlocal_count,
            "recognized_effect_count": recognized_effect_count,
        },
        findings,
        ["Absence of recognized effects does not prove purity."],
    )


def mutable_default(node: ast.AST) -> bool:
    """Return whether an argument default is a mutable literal or constructor."""

    if isinstance(node, (ast.List, ast.Dict, ast.Set)):
        return True
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _MUTABLE_CTORS
    )


def mutating_call(node: ast.AST) -> bool:
    """Return whether a call uses one of the legacy mutating method names."""

    return isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in _MUTATING_METHODS


def open_call(node: ast.AST) -> bool:
    """Return whether a call targets the builtin-spelled ``open`` name."""

    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open"


def attribute_write_targets(target: ast.AST) -> tuple[ast.Attribute, ...]:
    """Return attribute targets in an assignment target tree."""

    if isinstance(target, ast.Attribute):
        return (target,)
    if isinstance(target, (ast.Tuple, ast.List)):
        return tuple(
            attribute
            for element in target.elts
            for attribute in attribute_write_targets(element)
        )
    return ()


def attribute_write(node: ast.AST) -> bool:
    """Return whether a node is or contains an attribute assignment target."""

    return bool(attribute_write_targets(node))


def _count_attribute_writes(
    path: str,
    target: ast.AST,
    findings: list[dict[str, Any]],
    line: int,
    *,
    context: AnalysisContext | None = None,
) -> int:
    count = 0
    for attribute in attribute_write_targets(target):
        count += 1
        findings.append(
            _finding(
                context,
                {
                    "path": path,
                    "line": int(getattr(attribute, "lineno", line)),
                    "kind": "attribute_write",
                    "message": f"Attribute write observed: {attribute.attr}.",
                },
                ((path, attribute),),
            )
        )
    return count


def _factor(
    metrics: dict[str, int],
    findings: list[dict[str, Any]],
    limitations: list[str],
) -> dict[str, Any]:
    return {
        "status": "measured",
        "metrics": metrics,
        "findings": _sort_findings(findings),
        "limitations": limitations,
    }


def _finding(
    context: AnalysisContext | None,
    finding: dict[str, Any],
    node_refs: tuple[tuple[str, ast.AST], ...],
    ) -> dict[str, Any]:
    finding = dict(finding)
    detector_id = DETECTOR_IDS[finding["kind"]]
    if context is not None:
        path, node = node_refs[0]
        return enrich_finding(
            finding,
            context=context,
            indicator_id=detector_id,
            indicator_version=INDICATOR_VERSION,
            path=path,
            node=node,
            family="default",
            pattern={"kind": finding["kind"]},
            uncertainty=["static_candidate"],
            inspection_question=(
                "Inspect the owning boundary and intended state or resource lifetime before refactoring."
            ),
        )
    return finding


__all__ = [
    "DETECTOR_IDS",
    "FACTOR",
    "INDICATOR_ID",
    "INDICATOR_VERSION",
    "analyze",
    "attribute_write",
    "attribute_write_targets",
    "mutable_default",
    "mutating_call",
    "open_call",
]
