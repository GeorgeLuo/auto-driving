"""Computed readability candidates for conditional and short-circuit expressions."""

from __future__ import annotations

import ast
from collections import Counter
from collections.abc import Iterator
from typing import Any

from .context import AnalysisContext
from .functional_style import mutating_call
from .report import kind_finding, measured_factor, source_code


FACTOR = "patterns"
INDICATOR_ID = "structure.opaque_conditionals"
INDICATOR_VERSION = "1"
DETECTOR_IDS = {"opaque_conditional": f"{INDICATOR_ID}.opaque_conditional"}
REASONS = (
    "too_many_predicates",
    "repeated_subjects",
    "mixed_boolean_operators",
    "execution_in_evaluation",
)


def analyze(context: AnalysisContext) -> dict[str, Any]:
    """Measure syntax, without executing code or inferring helper effects."""

    findings = []
    metrics = {"opaque_conditional_count": 0}
    metrics.update({f"opaque_conditional_{reason}_count": 0 for reason in REASONS})
    for path, tree in context.trees(family="default").items():
        if not path.endswith(".py"):
            continue
        for expression, discarded in _candidates(tree):
            details = inspect_condition(expression, discarded=discarded)
            if not details["reasons"]:
                continue
            metrics["opaque_conditional_count"] += 1
            for reason in details["reasons"]:
                metrics[f"opaque_conditional_{reason}_count"] += 1
            findings.append(kind_finding(
                context,
                {
                    "path": path,
                    "line": expression.lineno,
                    "kind": "opaque_conditional",
                    "expression": source_code(context, path, expression),
                    **details,
                    "message": "Opaque conditional candidate: " + ", ".join(details["reasons"]) + ".",
                },
                detector_ids=DETECTOR_IDS,
                indicator_version=INDICATOR_VERSION,
                path=path,
                node=expression,
                pattern=details,
                uncertainty=["static_readability_candidate", "effects_and_bindings_not_proven"],
                inspection_question="Could named predicates or explicit execution steps make this expression easier to read?",
            ))
    return measured_factor(metrics, findings, [
        "Opaque conditionals are syntax-based inspection candidates, not proof of unreadability or side effects.",
        "Subject matching uses spellings, not aliases or bindings. Two-part safety guards are exempt from reuse only; helper purity and custom truthiness are not inferred.",
        "Effect recognition uses assignment/await/yield syntax, known mutator names, and discarded short-circuit calls; deferred lambda and generator bodies are excluded.",
    ])


def inspect_condition(expression: ast.expr, *, discarded: bool = False) -> dict[str, Any]:
    """Describe one expression; callers select its conditional context."""

    parts = list(_logical_parts(expression))
    predicates = list(_predicates(expression))
    predicate_count = _predicate_count(expression)
    subjects = Counter(subject for node in predicates for subject in _subjects(node))
    repeated = sorted(subject for subject, count in subjects.items() if count > 1)
    if _safety_guard(expression):
        repeated = []
    operators = {type(node.op) for node in parts if isinstance(node, ast.BoolOp)}
    effects = set()
    for node in _evaluated_nodes(expression):
        if isinstance(node, ast.NamedExpr):
            effects.add("assignment_expression")
        elif isinstance(node, (ast.Await, ast.Yield, ast.YieldFrom)):
            effects.add("await_or_yield")
        elif mutating_call(node):
            effects.add("mutating_call")
    if discarded and isinstance(expression, ast.BoolOp):
        if any(isinstance(node, ast.Call) for value in expression.values[1:] for node in _evaluated_nodes(value)):
            effects.add("short_circuit_action")
    matches = (predicate_count > 2, bool(repeated), len(operators) > 1, bool(effects))
    return {
        "reasons": [reason for reason, matched in zip(REASONS, matches) if matched],
        "predicate_count": predicate_count,
        "repeated_subjects": repeated,
        "execution_kinds": sorted(effects),
    }


def _logical_parts(node: ast.expr) -> Iterator[ast.expr]:
    yield node
    if isinstance(node, ast.BoolOp):
        for value in node.values:
            yield from _logical_parts(value)
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        yield from _logical_parts(node.operand)


def _predicates(node: ast.expr) -> Iterator[ast.expr]:
    for part in _logical_parts(node):
        if isinstance(part, ast.BoolOp):
            continue
        if isinstance(part, ast.UnaryOp) and isinstance(part.op, ast.Not):
            continue
        yield part


def _predicate_count(node: ast.expr) -> int:
    return sum(len(part.ops) if isinstance(part, ast.Compare) else 1 for part in _predicates(node))


def _candidates(tree: ast.Module) -> Iterator[tuple[ast.expr, bool]]:
    expressions: dict[ast.expr, bool] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.While, ast.IfExp, ast.Assert)):
            expressions.setdefault(node.test, False)
        elif isinstance(node, ast.comprehension):
            for test in node.ifs:
                expressions.setdefault(test, False)
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.BoolOp):
            expressions[node.value] = True
        elif isinstance(node, ast.BoolOp):
            expressions.setdefault(node, False)
    # Each Boolean node is already a candidate. Mark its immediate logical
    # children, through negations, without rescanning entire nested expressions.
    nested = set()
    for expression in expressions:
        if isinstance(expression, ast.BoolOp):
            children = expression.values
        elif isinstance(expression, ast.UnaryOp) and isinstance(expression.op, ast.Not):
            children = [expression.operand]
        else:
            continue
        for child in children:
            nested.add(child)
            while isinstance(child, ast.UnaryOp) and isinstance(child.op, ast.Not):
                child = child.operand
                nested.add(child)
    for expression, discarded in expressions.items():
        if expression not in nested:
            yield expression, discarded


def _evaluated_nodes(node: ast.AST) -> Iterator[ast.AST]:
    yield node
    if isinstance(node, (ast.Lambda, ast.GeneratorExp)):
        return
    for child in ast.iter_child_nodes(node):
        yield from _evaluated_nodes(child)


def _subject(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute) and _subject(node.value) is not None:
        return ast.unparse(node)
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and _subject(node.value) is not None:
        return ast.unparse(node)
    return None


def _subjects(node: ast.AST) -> set[str]:
    subject = _subject(node)
    if subject is not None:
        return {subject}
    if isinstance(node, (ast.Lambda, ast.GeneratorExp)):
        return set()
    if isinstance(node, ast.Call):
        # Callee names and type names are not evaluated subjects. A method's
        # receiver is: repeated mapping.get(...) checks still reuse the mapping.
        children = list(node.args) + [keyword.value for keyword in node.keywords]
        if isinstance(node.func, ast.Attribute):
            children.append(node.func.value)
        elif isinstance(node.func, ast.Name) and node.func.id in {"isinstance", "issubclass"}:
            children = list(node.args[:1])
    else:
        children = list(ast.iter_child_nodes(node))
    return set().union(*(_subjects(child) for child in children))


def _guard_subject(node: ast.expr, *, accepting: bool) -> str | None:
    """Match accepting ``and`` guards and rejecting ``or`` guards."""

    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return _guard_subject(node.operand, accepting=not accepting)
    if isinstance(node, ast.Compare) and len(node.ops) == 1:
        left, right = node.left, node.comparators[0]
        if isinstance(left, ast.Constant) and left.value is None:
            left, right = right, left
        if isinstance(right, ast.Constant) and right.value is None:
            expected = ast.IsNot if accepting else ast.Is
            if isinstance(node.ops[0], expected):
                return _subject(left)
        return None
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.keywords:
            return None
        if node.func.id == "isinstance" and len(node.args) == 2 and accepting:
            return _subject(node.args[0])
        if node.func.id == "bool" and len(node.args) == 1:
            return _guard_subject(node.args[0], accepting=accepting)
    if accepting:
        return _subject(node)
    return None


def _safety_guard(expression: ast.expr) -> bool:
    if not isinstance(expression, ast.BoolOp) or len(expression.values) != 2:
        return False
    guard, check = expression.values
    if _predicate_count(check) != 1:
        return False
    subject = _guard_subject(guard, accepting=isinstance(expression.op, ast.And))
    # Repeating the guard itself adds no protected evaluation.
    if ast.dump(guard) == ast.dump(check):
        return False
    return subject is not None and any(
        value == subject or value.startswith(subject + ".") or value.startswith(subject + "[")
        for value in _subjects(check)
    )


__all__ = ["FACTOR", "INDICATOR_ID", "INDICATOR_VERSION", "DETECTOR_IDS", "analyze", "inspect_condition"]
