"""Source rules owned by validation, independent of QCA's advice."""

from __future__ import annotations

import ast
import builtins
import collections.abc
import numbers
import re
import textwrap
import typing
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TypeGuard

from qca.analyzer import classify_source
from qca.evaluation import Context, Finding, definitions, finding, location
from scripts.validation.inspection import _parse, _sources

COPY_MARKER = "quality: allow-identical-body"
# Shorter bodies, such as stubs and one-line delegations, repeat by convention rather than by copying.
MIN_STATEMENTS = 3
RECOVERED_QUESTION = (
    "Was this value typed before it was stored in a dict or encoded? "
    "Could it travel typed instead of being checked or parsed again?"
)
# Checking a value read from a dict against these validates data; another class means it was typed before.
SHAPES = frozenset({*vars(builtins), *dir(collections.abc), *dir(typing), *dir(numbers)})
FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


@dataclass(frozen=True)
class Body:
    path: str
    symbol: str
    line: int
    end: int
    text: str
    override: bool


def object_casts(tree: Path, paths: list[str]) -> list[Finding]:
    """Reject casts that hide a declared contract from the type checker."""

    found = []
    for path, source in _sources(tree, paths).items():
        parsed = _parse(source)
        casts = {alias.asname or alias.name for node in ast.walk(parsed) if isinstance(node, ast.ImportFrom)
                 and node.module == "typing" for alias in node.names if alias.name == "cast"}
        modules = {alias.asname or alias.name for node in ast.walk(parsed) if isinstance(node, ast.Import)
                   for alias in node.names if alias.name == "typing"}
        for node in ast.walk(parsed):
            if _object_cast(node, casts, modules):
                found.append(finding(
                    "source-rules", "object-cast", location(path, node.lineno),
                    "Remove cast(object, ...). If the declared type holds, delete the check the cast hides; "
                    "otherwise validate the value at an object-typed input boundary.",
                    status="blocking",
                ))
    return found


def _object_cast(node: ast.AST, casts: set[str], modules: set[str]) -> bool:
    if not isinstance(node, ast.Call) or not node.args:
        return False
    target = node.args[0]
    if not isinstance(target, ast.Name) or target.id != "object":
        return False
    function = node.func
    if isinstance(function, ast.Name):
        return function.id in casts
    return (isinstance(function, ast.Attribute) and function.attr == "cast"
            and isinstance(function.value, ast.Name) and function.value.id in modules)


def recovered_types(tree: Path, paths: list[str]) -> list[Finding]:
    """Ask where production code recovers a typed value it stored untyped or encoded itself.

    Three shapes: ``from_dict`` of a ``to_dict()``; ``isinstance`` of a value
    read from a dict against a class rather than a builtin or shape; and an
    ``assert`` on the result of ``from_dict``.
    """

    found = []
    for path, source in _sources(tree, paths).items():
        if classify_source(path, source) != "production":
            continue
        found += [finding("source-rules", "recovered-type", location(path, node.lineno, node.end_lineno), RECOVERED_QUESTION)
                  for nodes in _scopes(_parse(source)) for node in _recovering(nodes)]
    return sorted(found, key=lambda item: (item["location"]["path"], item["location"]["line"]))


def _recovering(nodes: list[ast.AST]) -> Iterator[ast.AST]:
    """One scope's nodes that recover a type, judging each name by its latest assignment above them."""

    assigned: dict[str, ast.expr] = {}
    for node in nodes:
        if _recovered(node, assigned):
            yield node
        if isinstance(node, ast.Assign):
            assigned.update((target.id, node.value) for target in node.targets if isinstance(target, ast.Name))
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            for name in ast.walk(node.target):
                if isinstance(name, ast.Name):
                    assigned.pop(name.id, None)


def _recovered(node: ast.AST, assigned: dict[str, ast.expr]) -> bool:
    return _parses_own_encoding(node) or _asserts_parse(node, assigned) or _checks_stored_class(node, assigned)


def _parses_own_encoding(node: ast.AST) -> bool:
    if not _called(node, "from_dict"):
        return False
    return any(_called(inner, "to_dict") for argument in node.args for inner in ast.walk(argument))


def _asserts_parse(node: ast.AST, assigned: dict[str, ast.expr]) -> bool:
    if not isinstance(node, ast.Assert):
        return False
    return any(_called(assigned.get(name.id), "from_dict") for name in ast.walk(node.test) if isinstance(name, ast.Name))


def _checks_stored_class(node: ast.AST, assigned: dict[str, ast.expr]) -> bool:
    if not _isinstance_call(node):
        return False
    value, kinds = node.args
    if isinstance(value, ast.Name):
        value = assigned.get(value.id, value)
    return _read_from_dict(value) and any(map(_concrete_class, kinds.elts if isinstance(kinds, ast.Tuple) else [kinds]))


def _isinstance_call(node: ast.AST) -> TypeGuard[ast.Call]:
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
        return False
    return node.func.id == "isinstance" and len(node.args) == 2


def _concrete_class(kind: ast.expr) -> bool:
    """A class other than a builtin or shape; classes are CapWords, so a lowercase ``type`` parameter is not one."""

    if isinstance(kind, ast.Attribute):
        name = kind.attr
    elif isinstance(kind, ast.Name):
        name = kind.id
    else:
        return False
    return name[:1].isupper() and name not in SHAPES


def _called(node: ast.AST | None, method: str) -> TypeGuard[ast.Call]:
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
        return False
    return node.func.attr == method


def _read_from_dict(node: ast.expr) -> bool:
    """A keyed subscript or ``.get()``, or a conditional choosing one."""

    if isinstance(node, ast.IfExp):
        return _read_from_dict(node.body) or _read_from_dict(node.orelse)
    if isinstance(node, ast.Subscript):
        key = node.slice
        return isinstance(key, (ast.Name, ast.Attribute)) or (isinstance(key, ast.Constant) and isinstance(key.value, str))
    return _called(node, "get")


def _scopes(parsed: ast.AST) -> Iterator[list[ast.AST]]:
    """The module and each function, as the nodes each owns in source order; a nested function is its own scope."""

    for scope in [parsed, *(node for node in ast.walk(parsed) if isinstance(node, FUNCTIONS))]:
        yield list(_owned(scope))


def _owned(node: ast.AST) -> Iterator[ast.AST]:
    for child in ast.iter_child_nodes(node):
        yield child
        if not isinstance(child, FUNCTIONS):
            yield from _owned(child)


def identical_copies(context: Context, roots: tuple[str, ...]) -> list[Finding]:
    """Only newly added verbatim bodies in another production module block a change."""

    head = _bodies(context.head, roots)
    base = _bodies(context.base, roots) if context.base else []
    added = _added_bodies(base, head)
    provenance = {} if context.base is None else {"introduced": True}  # Whole files have nothing to compare with.
    groups: defaultdict[str, list[Body]] = defaultdict(list)
    for body in head:
        groups[body.text].append(body)
    return [
        finding(
            "source-rules", "identical-copy", location(body.path, body.line, body.end, body.symbol),
            f"Share this function body with its existing owner, or add '# {COPY_MARKER} -- ' "
            "followed by why the copies must differ.",
            status="blocking", evidence={**provenance, "override_marker": COPY_MARKER},
            related=[location(other.path, other.line, other.end, other.symbol) for other in groups[body.text]
                     if other.path != body.path],
        )
        for body in added
        if body.path in context.paths and not body.override
        and any(other.path != body.path for other in groups[body.text])
    ]


def _added_bodies(base: list[Body], head: list[Body]) -> list[Body]:
    """Match unchanged owners first, then renames within a module, before calling a body added."""
    owners = {(body.path, body.symbol, body.text) for body in base}
    available = Counter((body.path, body.text) for body in base)
    candidates = []
    for body in head:
        if (body.path, body.symbol, body.text) in owners:
            available[body.path, body.text] -= 1
        else:
            candidates.append(body)
    added = []
    for body in candidates:
        if available[body.path, body.text] > 0:
            available[body.path, body.text] -= 1
        else:
            added.append(body)
    return added


def _bodies(tree: Path, roots: tuple[str, ...]) -> list[Body]:
    bodies = []
    for path, source in _sources(tree, list(roots)).items():
        if classify_source(path, source) != "production":
            continue
        lines = source.splitlines()
        for node, symbol in definitions(_parse(source)):
            if isinstance(node, ast.ClassDef) or _statements(node) < MIN_STATEMENTS:
                continue
            end = node.end_lineno or node.lineno
            text = textwrap.dedent("\n".join(lines[node.body[0].lineno - 1:end]))
            marked = any(re.search(r"#\s*" + re.escape(COPY_MARKER) + r"\s+--\s+\S.+", line)
                         for line in lines[node.lineno - 1:end])
            bodies.append(Body(path, symbol, node.lineno, end, text, marked))
    return bodies


def _statements(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """Statements in a function body, nested ones included and a docstring left out."""

    body = node.body[1:] if ast.get_docstring(node) is not None else node.body
    return sum(isinstance(child, ast.stmt) for statement in body for child in ast.walk(statement))
