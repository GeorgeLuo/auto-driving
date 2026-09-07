"""Independent redundancy indicators for Python AST snapshots.

The public :func:`analyze` entry point consumes the shared AST context used by
the QCA indicator registry.  The lower-level tree pass accepts a plain
``path -> ast.Module`` mapping so the detector remains directly testable.  The returned factor keeps
the legacy metrics, findings, and limitations; detector identifiers and
context-provided evidence are additive finding fields.
"""

from __future__ import annotations

import ast
import hashlib
from collections import defaultdict
from collections.abc import Mapping
from typing import Any

from ..factors._utils import _sort_findings
from .context import AnalysisContext
from .evidence import LocatedNode, enrich_finding


FACTOR = "redundancy"
INDICATOR_ID = "structure.redundancy"
INDICATOR_VERSION = "1"
DETECTOR_IDS = {
    "repeated_branch": f"{INDICATOR_ID}.repeated_branch",
    "callable_clone": f"{INDICATOR_ID}.callable_clone",
}


def analyze(context: AnalysisContext) -> dict[str, Any]:
    """Measure redundancy in the trees supplied by ``context``."""

    trees = {
        path: tree
        for path, tree in context.trees(family="default").items()
        if path.endswith(".py")
    }
    return _analyze_trees(trees, context=context)


def _analyze_trees(
    trees: Mapping[str, ast.Module], *, context: Any = None
) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    stmt_counts: dict[str, int] = {}
    findings: list[dict[str, Any]] = []
    repeated_branch_count = 0

    for path in sorted(trees):
        tree = trees[path]
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if is_trivial_callable(node):
                    continue
                digest = callable_digest(node)
                entry = {
                    "path": path,
                    "line": int(node.lineno),
                    "end_line": int(node.end_lineno),
                    "name": node.name,
                    "code": ast.unparse(node),
                    "identifier_usage": identifier_usage(node),
                    # Keep the actual node until the clone finding is emitted.
                    # It is never exposed in the JSON finding.
                    "_node": node,
                }
                groups[digest].append(entry)
                stmt_counts[digest] = logical_stmt_count(node)
            elif isinstance(node, ast.If) and repeated_branch(node):
                repeated_branch_count += 1
                findings.append(
                    _finding(
                        context,
                        {
                            "path": path,
                            "line": int(node.lineno),
                            "kind": "repeated_branch",
                            "message": (
                                "If body and else branch have identical "
                                "statement structure."
                            ),
                        },
                        path=path,
                        node=node,
                        related_nodes=tuple(
                            [LocatedNode(path, statement) for statement in node.body]
                            + [LocatedNode(path, statement) for statement in node.orelse]
                        ),
                        pattern={
                            "branches": {
                                "body": [
                                    _source_code(context, path, statement)
                                    for statement in node.body
                                ],
                                "orelse": [
                                    _source_code(context, path, statement)
                                    for statement in node.orelse
                                ],
                            },
                            "shared_statement_structure": [
                                ast.dump(statement, include_attributes=False)
                                for statement in node.body
                            ],
                        },
                    )
                )

    clone_group_count = 0
    cloned_callable_count = 0
    duplicate_ast_loc = 0
    for digest in sorted(groups):
        occurrences = sorted(
            groups[digest],
            key=lambda item: (item["path"], item["line"], item["name"]),
        )
        if len(occurrences) < 2:
            continue
        clone_group_count += 1
        cloned_callable_count += len(occurrences)
        duplicate_ast_loc += stmt_counts[digest] * (len(occurrences) - 1)
        names = ", ".join(item["name"] for item in occurrences)
        usage_patterns = {
            tuple(item["identifier_usage"]["pattern"]) for item in occurrences
        }
        public_occurrences = [
            {key: value for key, value in item.items() if key != "_node"}
            for item in occurrences
        ]
        findings.append(
            _finding(
                context,
                {
                    "path": occurrences[0]["path"],
                    "line": occurrences[0]["line"],
                    "kind": "callable_clone",
                    "message": (
                        f"Approximate callable structure matches across "
                        f"{len(occurrences)} callables: {names}; inspect "
                        "identifier usage before sharing behavior."
                    ),
                    "identifier_usage_differs": len(usage_patterns) > 1,
                    "match_basis": "AST with all Name identifiers and parameter names erased",
                    "limitation": "Identifier usage records spelling equality in AST walk order, not lexical binding or behavioral equivalence.",
                    "occurrences": public_occurrences,
                    "paths": [item["path"] for item in occurrences],
                },
                path=occurrences[0]["path"],
                node=occurrences[0]["_node"],
                related_nodes=tuple(
                    LocatedNode(item["path"], item["_node"])
                    for item in occurrences[1:]
                ),
                pattern={
                    "matched_occurrences": [
                        {
                            "path": item["path"],
                            "name": item["name"],
                            "code": _source_code(context, item["path"], item["_node"]),
                        }
                        for item in occurrences
                    ],
                },
            )
        )

    return _factor(
        {
            "clone_group_count": clone_group_count,
            "cloned_callable_count": cloned_callable_count,
            "repeated_branch_count": repeated_branch_count,
            "duplicate_ast_loc": duplicate_ast_loc,
        },
        findings,
        ["Identifier normalization is approximate; renamed locals may still look identical."],
    )


def repeated_branch(node: ast.AST) -> bool:
    """Return whether an ``if`` has identical multi-statement branches."""

    if not isinstance(node, ast.If):
        return False
    if len(node.body) < 2 or len(node.orelse) < 2:
        return False
    body_dump = [ast.dump(stmt, include_attributes=False) for stmt in node.body]
    else_dump = [ast.dump(stmt, include_attributes=False) for stmt in node.orelse]
    return body_dump == else_dump


def is_callable_clone(
    left: ast.FunctionDef | ast.AsyncFunctionDef,
    right: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    """Return whether two nontrivial callables have the same normalized AST."""

    return (
        not is_trivial_callable(left)
        and not is_trivial_callable(right)
        and callable_digest(left) == callable_digest(right)
    )


def callable_clone(
    left: ast.FunctionDef | ast.AsyncFunctionDef,
    right: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    """Alias exposing the clone predicate as a detector-shaped callable."""

    return is_callable_clone(left, right)


def identifier_usage(node: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, Any]:
    """Expose equality distinctions discarded by broad clone matching."""

    names: dict[str, int] = {}
    pattern: list[int] = []
    for child in ast.walk(node):
        name = (
            child.id
            if isinstance(child, ast.Name)
            else child.arg
            if isinstance(child, ast.arg)
            else None
        )
        if name is not None:
            pattern.append(names.setdefault(name, len(names)))
    return {"names": list(names), "pattern": pattern}


def is_trivial_callable(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Return whether a callable contains only a stub-like trivial statement."""

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
    if isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Constant):
        return True
    return False


def callable_digest(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Return the legacy normalized callable digest."""

    normalized = _NameNormalizer().visit(_copy_ast(node))
    assert isinstance(normalized, (ast.FunctionDef, ast.AsyncFunctionDef))
    normalized.name = "_"
    payload = ast.dump(normalized, include_attributes=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def logical_stmt_count(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    return sum(
        1
        for child in ast.walk(node)
        if isinstance(child, ast.stmt) and child is not node
    )


class _NameNormalizer(ast.NodeTransformer):
    """Replace Name ids and parameter names; keep Attribute.attr intact."""

    def visit_Name(self, node: ast.Name) -> ast.Name:
        return ast.Name(id="_n", ctx=node.ctx)

    def visit_arg(self, node: ast.arg) -> ast.arg:
        self.generic_visit(node)
        return ast.arg(arg="_n", annotation=node.annotation)


def _copy_ast(node: Any) -> Any:
    if isinstance(node, ast.AST):
        return type(node)(**{field: _copy_ast(getattr(node, field)) for field in node._fields})
    if isinstance(node, list):
        return [_copy_ast(item) for item in node]
    return node


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


def _source_code(
    context: AnalysisContext | None,
    path: str,
    node: ast.AST,
) -> str:
    if context is not None:
        block = context.source_block(path, node, family="default")
        if block is not None:
            return block.code
    return ast.unparse(node)


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
    finding = dict(finding)
    detector_id = DETECTOR_IDS[finding["kind"]]
    if context is not None:
        return enrich_finding(
            finding,
            context=context,
            indicator_id=detector_id,
            indicator_version=INDICATOR_VERSION,
            path=path,
            node=node,
            family="default",
            related_nodes=related_nodes,
            pattern=pattern or {"kind": finding["kind"]},
            uncertainty=uncertainty or ["static_candidate"],
            inspection_question=(
                inspection_question
                or "Inspect identifier usage and behavior before sharing this candidate."
            ),
        )
    return finding


__all__ = [
    "DETECTOR_IDS",
    "FACTOR",
    "INDICATOR_ID",
    "INDICATOR_VERSION",
    "analyze",
    "callable_clone",
    "callable_digest",
    "identifier_usage",
    "is_callable_clone",
    "is_trivial_callable",
    "logical_stmt_count",
    "repeated_branch",
]
