"""Shared measured-factor and finding-enrichment glue for indicator modules."""

from __future__ import annotations

import ast
from collections.abc import Mapping, Sequence
from typing import Any

from ..factors._utils import _sort_findings
from .context import AnalysisContext
from .evidence import LocatedNode, enrich_finding


def measured_factor(
    metrics: dict[str, int],
    findings: list[dict[str, Any]],
    limitations: list[str],
) -> dict[str, Any]:
    """Return the legacy measured-factor dictionary with sorted findings."""

    return {
        "status": "measured",
        "metrics": metrics,
        "findings": _sort_findings(findings),
        "limitations": limitations,
    }


def source_code(
    context: AnalysisContext | None,
    path: str,
    node: ast.AST,
    *,
    family: str = "default",
) -> str:
    """Return exact source for ``node``, preferring context-sliced blocks."""

    if context is not None:
        block = context.source_block(path, node, family)
        if block is not None:
            return block.code
    return ast.unparse(node)


def kind_finding(
    context: AnalysisContext | None,
    finding: dict[str, Any],
    *,
    detector_ids: Mapping[str, str],
    indicator_version: str,
    path: str,
    node: ast.AST,
    family: str = "default",
    related_nodes: Sequence[LocatedNode] = (),
    pattern: Mapping[str, Any] | None = None,
    uncertainty: list[str] | None = None,
    inspection_question: str | None = None,
    primary_reason: str | None = None,
) -> dict[str, Any]:
    """Copy a detector finding and attach indicator identity plus evidence."""

    finding = dict(finding)
    detector_id = detector_ids[finding["kind"]]
    if context is None:
        return finding
    pattern_value = dict(pattern) if pattern is not None else {"kind": finding["kind"]}
    return enrich_finding(
        finding,
        context=context,
        indicator_id=detector_id,
        indicator_version=indicator_version,
        path=path,
        node=node,
        family=family,
        related_nodes=tuple(related_nodes),
        pattern=pattern_value,
        uncertainty=uncertainty or ["static_candidate"],
        inspection_question=inspection_question,
        primary_reason=primary_reason,
    )


def call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def source_expression(text: str, node: ast.AST) -> str:
    expression = ast.get_source_segment(text, node)
    if expression:
        return " ".join(expression.strip().split())
    try:
        return ast.unparse(node)
    except (AttributeError, ValueError):
        return type(node).__name__


def verification_inputs(
    context: AnalysisContext,
) -> tuple[list[tuple[str, str]], list[tuple[str, str, ast.AST]], list[dict[str, Any]], int]:
    if not isinstance(context, AnalysisContext):
        raise TypeError("context must be an AnalysisContext")
    ordered_sources = sorted(context.sources.items(), key=lambda item: item[0])
    paths = tuple(context.paths("verification"))
    trees = context.trees("verification")
    parsed = [(path, context.sources[path], trees[path]) for path in paths if path in trees]
    parse_errors: list[dict[str, Any]] = []
    for raw_path, raw_error in context.syntax_errors("verification").items():
        item = raw_error.to_dict()
        parse_errors.append(
            {
                "path": str(item.get("path", raw_path)),
                "line": int(item.get("line") or 0),
                "column": int(item.get("column") or 0),
                "message": str(item.get("message", "syntax error")),
            }
        )
    return ordered_sources, parsed, parse_errors, len(paths)
