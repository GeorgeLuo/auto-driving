"""Compatibility wrapper for the independently testable structure indicators."""

from __future__ import annotations

from typing import Any

from ..indicators.context import AnalysisContext
from ..indicators import functionality as _functionality_indicator
from ..indicators import functional_style as _functional_style_indicator
from ..indicators import patterns as _patterns_indicator
from ..indicators import redundancy as _redundancy_indicator
from ..indicators.evidence import enrich_finding
from ._utils import _norm, _sort_findings


def analyze_structure(
    sources: dict[str, str],
    *,
    revision: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Measure structure factors through the shared AST context.

    ``sources`` keeps the historic path-to-text API.  Additive indicator
    identity and source evidence are carried on findings while the historic
    factor fields, metrics, and limitations remain unchanged.  Syntax failures
    are retained on every factor and never stop other files.
    """

    source_map = {
        _norm(path): text if isinstance(text, str) else str(text)
        for path, text in sources.items()
    }
    context = AnalysisContext.from_sources(source_map, revision=revision)
    return analyze_structure_context(context)


def analyze_structure_context(
    context: AnalysisContext,
) -> dict[str, dict[str, Any]]:
    """Run all four structure indicators over one shared context.

    The adapter owns the legacy syntax-error fan-out so a registry can use one
    context without making each indicator repeat parse-error handling.
    """

    factors = {
        "redundancy": _redundancy_indicator.analyze(context),
        "patterns": _patterns_indicator.analyze(context),
        "functional_style": _functional_style_indicator.analyze(context),
        "functionality": _functionality_indicator.analyze(context),
    }
    errors = {
        path: error
        for path, error in context.syntax_errors(family="default").items()
        if path.endswith(".py")
    }
    for error in errors.values():
        finding = enrich_finding(
            {
                "path": error.path,
                "line": int(error.line or 1),
                "kind": "syntax_error",
                "message": f"Python source could not be parsed: {error.message}",
            },
            context=context,
            indicator_id="structure.syntax_error",
            indicator_version="1",
            path=error.path,
            family="default",
            pattern={"kind": "syntax_error", "path": error.path},
            uncertainty=["parse_error"],
            inspection_question="Repair the syntax before interpreting structure findings from this file.",
            primary_reason="syntax_error_no_ast_node",
        )
        for factor in factors.values():
            factor["findings"].append(dict(finding))
            factor["limitations"].append(
                f"{error.path} could not be parsed; structure observations "
                "from that file are unavailable."
            )
    for factor in factors.values():
        factor["findings"] = _sort_findings(factor["findings"])
        factor["limitations"] = sorted(set(factor["limitations"]))
    return factors


__all__ = ["analyze_structure", "analyze_structure_context"]
