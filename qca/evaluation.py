"""Run evaluators against their natural context and collect flat findings."""

from collections.abc import Callable, Iterable

FIELDS = {"type", "location", "question", "source", "status", "related", "evidence"}


def finding(kind: str, location: dict, question: str, tool: str, rule: str,
            *, evidence: dict, related: list | None = None, status: str = "review") -> dict:
    return {"type": kind, "location": location, "question": question,
            "source": {"tool": tool, "rule": rule}, "status": status,
            "related": related or [], "evidence": evidence}


def validate_finding(item: object) -> None:
    if not isinstance(item, dict) or set(item) != FIELDS:
        raise ValueError("Evaluator returned a finding with an invalid shape")
    types = {"location": dict, "source": dict, "related": list, "evidence": dict,
             "type": str, "question": str}
    if any(not isinstance(item[key], expected) for key, expected in types.items()):
        raise ValueError("Evaluator returned an invalid finding")
    if not item["type"] or not item["question"] or item["status"] not in {"blocking", "review"}:
        raise ValueError("Evaluator returned an invalid finding")
    location, source = item["location"], item["source"]
    if not (location.get("path") or {"start", "end"} <= location.keys()):
        raise ValueError("Finding is missing a location")
    if not source.get("tool") or not source.get("rule"):
        raise ValueError("Finding is missing a source")


def evaluate(evaluations: Iterable[tuple[Callable[..., list[dict]], object]],
             *, findings: list[dict] | None = None) -> list[dict]:
    """Run each evaluator on its own context; a finding is not a verdict."""
    results = findings if findings is not None else []
    for evaluator, context in evaluations:
        for item in evaluator(context):
            validate_finding(item)
            results.append(item)
    return results
