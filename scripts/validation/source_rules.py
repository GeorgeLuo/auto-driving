"""Blocking source rules owned by validation, independent of QCA's advice."""

from __future__ import annotations

import ast
import re
import textwrap
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from qca.analyzer import classify_source
from qca.evaluation import Context, Finding, definitions, finding, location
from scripts.validation.inspection import _parse, _sources

COPY_MARKER = "quality: allow-identical-body"


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
                    "Remove cast(object, ...); validate untrusted data at an object-typed input boundary.",
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
    prior = {(body.path, body.symbol, body.text) for body in _bodies(context.base, roots)} if context.base else set()
    groups: defaultdict[str, list[Body]] = defaultdict(list)
    for body in head:
        groups[body.text].append(body)
    return [
        finding(
            "source-rules", "identical-copy", location(body.path, body.line, body.end, body.symbol),
            f"Share this function body with its existing owner, or add '# {COPY_MARKER} -- ' "
            "followed by why the copies must differ.",
            status="blocking", evidence={"introduced": True, "override_marker": COPY_MARKER},
            related=[location(other.path, other.line, other.end, other.symbol) for other in groups[body.text]
                     if other.path != body.path],
        )
        for body in head
        if body.path in context.paths and (body.path, body.symbol, body.text) not in prior and not body.override
        and any(other.path != body.path for other in groups[body.text])
    ]


def _bodies(tree: Path, roots: tuple[str, ...]) -> list[Body]:
    bodies = []
    for path, source in _sources(tree, list(roots)).items():
        if classify_source(path, source) != "production":
            continue
        lines = source.splitlines()
        for node, symbol in definitions(_parse(source)):
            if isinstance(node, ast.ClassDef):
                continue
            end = node.end_lineno or node.lineno
            text = textwrap.dedent("\n".join(lines[node.body[0].lineno - 1:end]))
            marked = any(re.search(r"#\s*" + re.escape(COPY_MARKER) + r"\s+--\s+\S.+", line)
                         for line in lines[node.lineno - 1:end])
            bodies.append(Body(path, symbol, node.lineno, end, text, marked))
    return bodies
