"""Evaluate caller-supplied text and return reviewable findings.

The context is exactly the supplied string. Evaluators decide what that text
means; this interface has no repository, file, diff, or language assumptions.
Text ranges use zero-based, half-open character offsets into the input.
"""

from collections.abc import Callable, Iterable


def finding(kind: str, location: dict, question: str, tool: str, rule: str,
            *, evidence: dict, related: list | None = None, status: str = "review") -> dict:
    return {"type": kind, "location": location, "question": question,
            "source": {"tool": tool, "rule": rule}, "status": status,
            "related": related or [], "evidence": evidence}


def evaluate(text: str, evaluators: Iterable[Callable[[str], list[dict]]]) -> list[dict]:
    """Run each evaluator on the same text; never treat a candidate as a verdict."""
    return [item for evaluator in evaluators for item in evaluator(text)]
