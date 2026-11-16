"""Blocking source rules owned by validation, independent of QCA's advice."""

from __future__ import annotations

import ast
import re
import textwrap
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from qca.analyzer import classify_source
from qca.evaluation import Context, Finding, definitions, finding, location
from scripts.validation.inspection import _parse, _sources

COPY_MARKER = "quality: allow-identical-body"
# Shorter bodies, such as stubs and one-line delegations, repeat by convention rather than by copying.
MIN_STATEMENTS = 3


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
