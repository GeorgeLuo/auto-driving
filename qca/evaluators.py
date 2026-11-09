"""QCA's own evaluators, returning the findings shape of ``qca.evaluation``."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from .analyzer import AnalyzerConfig, CallableMetrics, analyze_sources
from .evaluation import Finding, Location, finding, location

LIMITS = {"cyclomatic_complexity": 10, "logical_loc": 50, "max_nesting": 5}
QUESTIONS = {
    "cyclomatic_complexity": "Could this function make fewer decisions, or name the ones it makes?",
    "logical_loc": "Could this function be shorter, or split along its steps?",
    "max_nesting": "Could early exits or extracted steps flatten this nesting?",
}
PATTERNS = frozenset({"opaque_conditional", "redundant_any_guard", "swallowed_exception"})


def structure(tree: Path, paths: Sequence[str]) -> list[Finding]:
    """Complex callables, conditional and error-handling patterns, and clones in Python ``paths``."""

    sources = {
        path: (tree / path).read_text(encoding="utf-8", errors="replace")
        for path in paths
        if path.endswith((".py", ".pyi"))
    }
    if not sources:
        return []
    report = analyze_sources(sources, config=AnalyzerConfig(languages=("python",)))
    candidates = [item for item in report.factors["patterns"]["findings"] if item["kind"] in PATTERNS]
    candidates += report.factors["redundancy"]["findings"]
    return [
        *(_candidate(item) for item in candidates),
        *(_complexity(callable_, metric) for callable_ in report.head.callables for metric in LIMITS
          if getattr(callable_, metric) > LIMITS[metric]),
    ]


def _candidate(item: dict) -> Finding:  # A factor finding from a QCA report.
    evidence = item["evidence"]
    return finding(
        "qca",
        item["indicator"]["id"],
        _location(evidence["primary"]),
        evidence["inspection_question"],
        evidence={**evidence["pattern"], "uncertainty": evidence["uncertainty"]},
        related=[_location(block) for block in evidence["related"]],
    )


def _location(block: dict) -> Location:  # An evidence block from a QCA report.
    span = block["range"]
    return location(block["path"], span["start"]["line"], span["end"]["line"], ".".join(block["symbols"]))


def _complexity(callable_: CallableMetrics, metric: str) -> Finding:
    return finding(
        "qca",
        f"structure.complexity.{metric}",
        location(callable_.path, callable_.start_line, callable_.end_line, callable_.qualified_name),
        QUESTIONS[metric],
        measure={"value": getattr(callable_, metric), "limit": LIMITS[metric]},
    )
