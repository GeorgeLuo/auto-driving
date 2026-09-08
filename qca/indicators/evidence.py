"""Machine-readable source evidence for QCA indicator findings.

Indicators retain their existing factor dictionaries.  This module only adds
an ``indicator`` identity and an ``evidence`` object to an individual finding
when a detector has an explicit AST node to cite.  It never infers a node from
the finding's line number: line-only findings receive a null primary block and
an explanation of why no exact range was attached.
"""

from __future__ import annotations

import ast
import copy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .context import AnalysisContext, SourceBlock, normalize_path


@dataclass(frozen=True)
class LocatedNode:
    """An explicit path/node pair used for related source evidence."""

    path: str
    node: ast.AST
    family: str = "default"


def build_evidence(
    context: AnalysisContext,
    *,
    path: str | None = None,
    node: ast.AST | None = None,
    family: str = "default",
    related_nodes: Sequence[LocatedNode] = (),
    pattern: dict[str, Any] | None = None,
    uncertainty: list[str] | str | None = None,
    inspection_question: str | None = None,
    primary_reason: str | None = None,
) -> dict[str, Any]:
    """Build one JSON-safe source-evidence record.

    ``node`` and every related AST value must be supplied explicitly.  A path
    and line number alone are intentionally insufficient because several AST
    nodes can occupy one line.  The revision is carried by the context and is
    therefore ``None`` when the caller does not know it.
    """

    if not isinstance(context, AnalysisContext):
        raise TypeError("context must be an AnalysisContext")
    resolved_primary: SourceBlock | None = None
    resolved_reason = primary_reason
    if resolved_primary is None and node is not None:
        if path is None:
            resolved_reason = resolved_reason or "source_path_required_for_primary_node"
        else:
            resolved_primary = context.source_block(normalize_path(path), node, family)
            if resolved_primary is None:
                resolved_reason = resolved_reason or "source_range_unavailable"
    elif resolved_primary is None:
        resolved_reason = resolved_reason or "no_primary_node_supplied"

    related: list[dict[str, Any]] = []
    related_reasons: list[str] = []
    for index, candidate in enumerate(related_nodes):
        if not isinstance(candidate, LocatedNode):
            related_reasons.append(f"related[{index}]:located_node_required")
            continue
        block = context.source_block(
            normalize_path(candidate.path), candidate.node, candidate.family
        )
        if block is None:
            related_reasons.append(f"related[{index}]:source_range_unavailable")
            continue
        related.append(block.to_dict())

    if pattern is None:
        pattern_value = {}
    elif not isinstance(pattern, dict):
        raise TypeError("pattern must be a mapping or None")
    else:
        pattern_value = copy.deepcopy(pattern)

    if uncertainty is None:
        uncertainty_value: list[str] = []
    elif isinstance(uncertainty, str):
        # Existing detector adapters used one concise uncertainty label.  Keep
        # that call shape compatible while storing the stable list form.
        uncertainty_value = [uncertainty]
    elif not isinstance(uncertainty, list) or not all(isinstance(item, str) for item in uncertainty):
        raise TypeError("uncertainty must be a list of strings or a string")
    else:
        uncertainty_value = list(uncertainty)
    if inspection_question is not None and not isinstance(inspection_question, str):
        raise TypeError("inspection_question must be a string or None")

    result: dict[str, Any] = {
        "revision": context.revision,
        "primary": resolved_primary.to_dict() if resolved_primary is not None else None,
        "related": related,
        "pattern": pattern_value,
        "uncertainty": uncertainty_value,
        "inspection_question": inspection_question,
        "primary_reason": resolved_reason,
    }
    if related_reasons:
        result["related_reasons"] = related_reasons
    return result


def enrich_finding(
    finding: Mapping[str, Any],
    *,
    context: AnalysisContext,
    indicator_id: str,
    indicator_version: str,
    path: str | None = None,
    node: ast.AST | None = None,
    family: str = "default",
    related_nodes: Sequence[LocatedNode] = (),
    pattern: dict[str, Any] | None = None,
    uncertainty: list[str] | str | None = None,
    inspection_question: str | None = None,
    primary_reason: str | None = None,
) -> dict[str, Any]:
    """Copy a legacy finding and attach stable identity plus source evidence."""

    if not isinstance(finding, Mapping):
        raise TypeError("finding must be a mapping")
    if not isinstance(indicator_id, str) or not indicator_id:
        raise ValueError("indicator_id must be a non-empty string")
    if not isinstance(indicator_version, str) or not indicator_version:
        raise ValueError("indicator_version must be a non-empty string")

    result = copy.deepcopy(dict(finding))
    result["indicator"] = {"id": indicator_id, "version": indicator_version}
    result["evidence"] = build_evidence(
        context,
        path=path,
        node=node,
        family=family,
        related_nodes=related_nodes,
        pattern=pattern,
        uncertainty=uncertainty,
        inspection_question=inspection_question,
        primary_reason=primary_reason,
    )
    return result


__all__ = [
    "LocatedNode",
    "build_evidence",
    "enrich_finding",
]
