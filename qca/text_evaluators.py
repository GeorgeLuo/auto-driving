"""Text evaluators backed by QCA's existing Python pattern indicators."""

import ast

from .evaluation import finding
from .indicators import AnalysisContext, patterns


def offset(text: str, line: int, column: int) -> int:
    """Convert one-based lines and zero-based character columns to offsets."""
    lines = text.split("\n")
    return sum(len(part) + 1 for part in lines[:line - 1]) + column


def python_patterns(text: str) -> list[dict]:
    """Evaluate Python text; malformed input raises rather than looking clean."""
    ast.parse(text)
    context = AnalysisContext.from_sources({"input.py": text})
    records = []
    for item in patterns.analyze(context)["findings"]:
        if item["kind"] not in {"opaque_conditional", "redundant_any_guard", "swallowed_exception"}:
            continue
        evidence = item["evidence"]
        span = evidence["primary"]["range"]
        start, end = span["start"], span["end"]
        location = {"start": offset(text, start["line"], start["column"]),
                    "end": offset(text, end["line"], end["column"])}
        records.append(finding("qca." + item["kind"], location, evidence["inspection_question"],
                               "qca", item["indicator"]["id"], evidence=evidence["pattern"]))
    return records
