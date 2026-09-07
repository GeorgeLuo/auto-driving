"""Independent stub and unreachable-code indicators."""

from __future__ import annotations

import ast
from collections.abc import Mapping
from typing import Any

from ..factors._utils import _sort_findings
from .context import AnalysisContext
from .evidence import enrich_finding


FACTOR = "functionality"
INDICATOR_ID = "structure.functionality"
INDICATOR_VERSION = "1"
DETECTOR_IDS = {
    "stub": f"{INDICATOR_ID}.stub",
    "unreachable": f"{INDICATOR_ID}.unreachable",
}


def analyze(context: AnalysisContext) -> dict[str, Any]:
    """Measure stubs and obvious same-body unreachable statements."""

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
    stub_count = 0
    unreachable_count = 0

    for path in sorted(trees):
        tree = trees[path]
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and stub(node):
                stub_count += 1
                findings.append(
                    _finding(
                        context,
                        {
                            "path": path,
                            "line": int(node.lineno),
                            "kind": "stub",
                            "message": f"Stub callable observed: {node.name}.",
                            "name": node.name,
                        },
                        ((path, node),),
                    )
                )

        unreachable_count += _collect_unreachable(
            path, tree.body, findings, context=context
        )
        for node in ast.walk(tree):
            if node is tree:
                continue
            for attr in ("body", "orelse", "finalbody"):
                block = getattr(node, attr, None)
                if isinstance(block, list) and block and isinstance(block[0], ast.stmt):
                    unreachable_count += _collect_unreachable(
                        path, block, findings, context=context
                    )

    return _factor(
        {
            "stub_count": stub_count,
            "unreachable_count": unreachable_count,
        },
        findings,
        ["Inspect intentional hooks and protocols before removing code."],
    )


def stub(node: ast.AST) -> bool:
    """Return whether a callable contains one of the legacy stub forms."""

    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    if len(node.body) != 1:
        return False
    stmt = node.body[0]
    if isinstance(stmt, ast.Pass):
        return True
    if (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and stmt.value.value is ...
    ):
        return True
    if isinstance(stmt, ast.Raise) and stmt.exc is not None:
        exc = stmt.exc
        if isinstance(exc, ast.Name) and exc.id in {"NotImplementedError", "NotImplemented"}:
            return True
        if isinstance(exc, ast.Call) and isinstance(exc.func, ast.Name):
            if exc.func.id in {"NotImplementedError", "NotImplemented"}:
                return True
    return False


def is_terminal(node: ast.AST) -> bool:
    """Return whether a statement terminates the current body scan."""

    return isinstance(node, (ast.Return, ast.Raise))


def collect_unreachable(body: list[ast.stmt]) -> tuple[ast.stmt, ...]:
    """Return statements after the first return/raise in one body list."""

    unreachable: list[ast.stmt] = []
    seen_terminal = False
    for stmt in body:
        if seen_terminal:
            unreachable.append(stmt)
            continue
        if is_terminal(stmt):
            seen_terminal = True
    return tuple(unreachable)


def unreachable(body: list[ast.stmt]) -> bool:
    """Return whether a body contains at least one obviously unreachable statement."""

    return bool(collect_unreachable(body))


def _collect_unreachable(
    path: str,
    body: list[ast.stmt],
    findings: list[dict[str, Any]],
    *,
    context: AnalysisContext | None = None,
) -> int:
    statements = collect_unreachable(body)
    for stmt in statements:
        findings.append(
            _finding(
                context,
                {
                    "path": path,
                    "line": int(stmt.lineno),
                    "kind": "unreachable",
                    "message": (
                        "Statement appears after a terminal return or raise "
                        "in the same body list."
                    ),
                },
                ((path, stmt),),
            )
        )
    return len(statements)


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
                "Inspect intentional hooks, protocols, and control-flow intent before changing this code."
            ),
        )
    return finding


__all__ = [
    "DETECTOR_IDS",
    "FACTOR",
    "INDICATOR_ID",
    "INDICATOR_VERSION",
    "analyze",
    "collect_unreachable",
    "is_terminal",
    "stub",
    "unreachable",
]
