"""Change-level review questions and report-only per-file totals."""

from __future__ import annotations

import ast
import tomllib
from collections import defaultdict
from pathlib import Path

from qca.analyzer import AnalyzerConfig, analyze_sources
from qca.evaluation import Context, Finding, finding, in_scope, location
from scripts.validation.inspection import _parse
from qca.evaluation import definitions

LIFETIME_QUESTION = (
    "This change touches state that outlives a call, survives replacement, or is written outside the owning call. "
    "Which value is authoritative, which transitions change, and which test drives each through its real callers?"
)
EXTRACTION_QUESTION = "What does each new function own, and what can its callers stop deciding?"


def complexity_totals(tree: Path, paths: tuple[str, ...]) -> dict[str, int]:
    sources = _python_sources(tree, paths)
    totals: defaultdict[str, int] = defaultdict(int, {path: 0 for path in sources})
    if sources:
        report = analyze_sources(sources, config=AnalyzerConfig(languages=("python",)))
        for function in report.head.callables:
            totals[function.path] += function.cyclomatic_complexity
    return dict(totals)


def runtime_type_checks(tree: Path, paths: tuple[str, ...]) -> dict[str, int]:
    """``isinstance`` and ``issubclass`` calls per file; typing a boundary should lower them."""

    return {path: sum(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                      and node.func.id in {"isinstance", "issubclass"} for node in ast.walk(_parse(source)))
            for path, source in _python_sources(tree, paths).items()}


def _python_sources(tree: Path, paths: tuple[str, ...]) -> dict[str, str]:
    return {path: (tree / path).read_text() for path in paths if path.endswith(".py") and (tree / path).is_file()}


def change_questions(context: Context) -> list[Finding]:
    if context.base is None:
        return []
    tools = tomllib.loads((context.head / "pyproject.toml").read_text())["tool"]
    lifetime = tuple(tools.get("validation", {}).get("lifecycle_paths", []))
    found = [finding("review", "state-lifetime", location(path, 1), LIFETIME_QUESTION,
                     evidence={"introduced": True})
             for path in context.paths if in_scope(path, lifetime)]
    for path in context.paths:
        if path.endswith(".py") and (context.head / path).is_file() and (context.base / path).is_file():
            found.extend(_extraction(context, path))
    return found


def _extraction(context: Context, path: str) -> list[Finding]:
    from qca.evaluators import structure

    old = [item for item in structure(context.base, [path]) if item["rule"].startswith("structure.complexity.")]
    remaining = {(item["location"].get("symbol"), item["rule"]) for item in structure(context.head, [path])
                 if item["rule"].startswith("structure.complexity.")}
    previous = {name for _, name in definitions(_parse((context.base / path).read_text()))}
    added = [name for node, name in definitions(_parse((context.head / path).read_text()))
             if name not in previous and not isinstance(node, ast.ClassDef)]
    return [finding("review", "complexity-extraction", item["location"], EXTRACTION_QUESTION,
                    evidence={"introduced": True, "new_functions": added})
            for item in old if (item["location"].get("symbol"), item["rule"]) not in remaining and added]
