"""Independent static lifecycle indicators for QCA.

Lifecycle names and call sites are review leads only.  They do not establish
that a resource was started, stopped, reset, or cleaned up, nor do they prove
that any of those operations are symmetric at runtime.
"""

from __future__ import annotations

import ast
from typing import Any

from .context import AnalysisContext
from .evidence import enrich_finding


FACTOR = "lifecycle"
INDICATOR_ID = "verification.lifecycle"
INDICATOR_VERSION = "1"

DETECTOR_IDS = {
    "lifecycle_effect_site": f"{INDICATOR_ID}.effect_site",
}

MAX_LIFECYCLE_SITES = 128
_LIFECYCLE_ALIASES: dict[str, set[str]] = {
    "start": {"start", "begin", "launch"},
    "stop": {"stop", "shutdown", "halt", "terminate", "cancel"},
    "reset": {"reset", "restart", "clear"},
    "cleanup": {"cleanup", "clean_up", "teardown", "close", "dispose", "release"},
}
_LIFECYCLE_BY_NAME = {
    alias: operation for operation, aliases in _LIFECYCLE_ALIASES.items() for alias in aliases
}
_LIFECYCLE_SUFFIXES = ("_locked", "_action", "_now", "_async")


def analyze(context: AnalysisContext) -> dict[str, Any]:
    """Return the legacy ``lifecycle`` factor for ``context``."""

    _, parsed, parse_errors, _ = _verification_inputs(context)
    return _lifecycle_factor(parsed, parse_errors, context=context)


def lifecycle_operation(name: str) -> str | None:
    """Map one recognized lifecycle-shaped name to its operation."""

    normalized = name.strip().lower().lstrip("_")
    for suffix in _LIFECYCLE_SUFFIXES:
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
            break
    return _LIFECYCLE_BY_NAME.get(normalized)


def is_lifecycle_definition(node: ast.AST) -> str | None:
    """Return the operation for a lifecycle-shaped definition, if any."""

    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None
    return lifecycle_operation(node.name)


def is_lifecycle_call(node: ast.AST) -> str | None:
    """Return the operation for a lifecycle-shaped call, if any."""

    if not isinstance(node, ast.Call):
        return None
    return lifecycle_operation(_call_name(node.func) or "")


def _lifecycle_factor(
    parsed: list[tuple[str, str, ast.AST]],
    parse_errors: list[dict[str, Any]],
    *,
    context: AnalysisContext | None = None,
) -> dict[str, Any]:
    by_kind = {
        operation: {"definitions": 0, "calls": 0, "sites": 0}
        for operation in _LIFECYCLE_ALIASES
    }
    sites: list[dict[str, Any]] = []
    for path, text, tree in parsed:
        for node in ast.walk(tree):
            operation = is_lifecycle_definition(node)
            if operation is not None:
                by_kind[operation]["definitions"] += 1
                _append_lifecycle_site(
                    sites,
                    context=context,
                    path=path,
                    node=node,
                    operation=operation,
                    site_type="definition",
                    text=text,
                )
            elif isinstance(node, ast.Call):
                operation = is_lifecycle_call(node)
                if operation is not None:
                    by_kind[operation]["calls"] += 1
                    _append_lifecycle_site(
                        sites,
                        context=context,
                        path=path,
                        node=node,
                        operation=operation,
                        site_type="call",
                        text=text,
                    )

    for values in by_kind.values():
        values["sites"] = values["definitions"] + values["calls"]
    total_sites = sum(values["sites"] for values in by_kind.values())
    limitations = [
        "Recognized names and call sites are static indicators only; they do not prove start/stop/reset/cleanup effects or symmetry.",
        "No runtime lifecycle sequence, resource ownership, or teardown outcome was measured.",
    ]
    if len(sites) < total_sites:
        limitations.append(
            f"Lifecycle site details are limited to the first {MAX_LIFECYCLE_SITES}; counts remain parser-derived."
        )
    if parse_errors:
        limitations.append(
            f"{len(parse_errors)} Python file(s) could not be parsed and were excluded from lifecycle AST counts."
        )
    metrics = {
        "recognized_site_count": total_sites,
        "effect_site_count": total_sites,
        "definitions_count": sum(values["definitions"] for values in by_kind.values()),
        "calls_count": sum(values["calls"] for values in by_kind.values()),
        "start_definition_count": by_kind["start"]["definitions"],
        "start_call_count": by_kind["start"]["calls"],
        "start_site_count": by_kind["start"]["sites"],
        "stop_definition_count": by_kind["stop"]["definitions"],
        "stop_call_count": by_kind["stop"]["calls"],
        "stop_site_count": by_kind["stop"]["sites"],
        "reset_definition_count": by_kind["reset"]["definitions"],
        "reset_call_count": by_kind["reset"]["calls"],
        "reset_site_count": by_kind["reset"]["sites"],
        "cleanup_definition_count": by_kind["cleanup"]["definitions"],
        "cleanup_call_count": by_kind["cleanup"]["calls"],
        "cleanup_site_count": by_kind["cleanup"]["sites"],
        "site_count": len(sites),
        "parse_error_count": len(parse_errors),
    }
    return {
        "status": "measured",
        "metrics": metrics,
        "findings": sites,
        "details": {
            "by_kind": by_kind,
            "site_limit": MAX_LIFECYCLE_SITES,
            "sites_are_complete": len(sites) == total_sites,
        },
        "limitations": limitations,
    }


def _append_lifecycle_site(
    sites: list[dict[str, Any]],
    *,
    context: AnalysisContext | None,
    path: str,
    node: ast.AST,
    operation: str,
    site_type: str,
    text: str,
) -> None:
    if len(sites) >= MAX_LIFECYCLE_SITES:
        return
    message = (
        "Static lifecycle name/call site only; inspect the runtime effect and paired cleanup behavior separately."
    )
    finding: dict[str, Any] = {
        "kind": "lifecycle_effect_site",
        "operation": operation,
        "site_type": site_type,
        "path": path,
        "line": int(getattr(node, "lineno", 0) or 0),
        "column": int((getattr(node, "col_offset", 0) or 0) + 1),
        "name": _source_expression(text, node),
        "message": message,
    }
    if isinstance(context, AnalysisContext):
        finding = enrich_finding(
            finding,
            context=context,
            indicator_id=DETECTOR_IDS["lifecycle_effect_site"],
            indicator_version=INDICATOR_VERSION,
            path=path,
            node=node,
            family="verification",
            pattern={"operation": operation, "site_type": site_type},
            uncertainty=["name_or_call_site_only"],
            inspection_question=(
                "Inspect the runtime effect and paired cleanup behavior separately."
            ),
        )
    sites.append(finding)


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _source_expression(text: str, node: ast.AST) -> str:
    expression = ast.get_source_segment(text, node)
    if expression:
        return " ".join(expression.strip().split())
    try:
        return ast.unparse(node)
    except (AttributeError, ValueError):
        return type(node).__name__


def _verification_inputs(
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


__all__ = [
    "DETECTOR_IDS",
    "FACTOR",
    "INDICATOR_ID",
    "INDICATOR_VERSION",
    "MAX_LIFECYCLE_SITES",
    "analyze",
    "is_lifecycle_call",
    "is_lifecycle_definition",
    "lifecycle_operation",
]
