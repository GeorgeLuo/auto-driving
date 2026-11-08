"""Syntax and reference evidence for committed Python sources; never imports them."""

from __future__ import annotations

import ast
import builtins
from dataclasses import dataclass


@dataclass(frozen=True)
class Symbol:
    path: str
    name: str
    kind: str
    line: int
    end_line: int
    code: str
    shape: str
    calls: frozenset[str]

    @property
    def key(self) -> tuple[str, str]:
        return self.path, self.name


def call_spelling(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = call_spelling(node.value)
        return f"{parent}.{node.attr}" if parent else ""
    return ""


def direct_calls(node: ast.FunctionDef | ast.AsyncFunctionDef) -> frozenset[str]:
    """Exclude nested definitions and builtins from identifier comparisons."""
    calls: set[str] = set()

    def visit(child: ast.AST) -> None:
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            return
        if isinstance(child, ast.Call):
            name = call_spelling(child.func)
            if name and name not in vars(builtins):
                calls.add(name)
        for descendant in ast.iter_child_nodes(child):
            visit(descendant)

    # Signatures and decorators are not part of the executed function body.
    for statement in node.body:
        visit(statement)
    return frozenset(calls)


def symbols(path: str, source: str) -> list[Symbol]:
    tree = ast.parse(source, filename=path)
    lines = source.splitlines()
    found: list[Symbol] = []

    def visit(node: ast.AST, owner: str = "") -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            owner = f"{owner}.{node.name}" if owner else node.name
            end = node.end_lineno or node.lineno
            kind = "class" if isinstance(node, ast.ClassDef) else "callable"
            found.append(Symbol(
                path, owner, kind, node.lineno, end,
                "\n".join(lines[node.lineno - 1:end]),
                ast.dump(node, include_attributes=False),
                frozenset() if isinstance(node, ast.ClassDef) else direct_calls(node),
            ))
        for child in ast.iter_child_nodes(node):
            visit(child, owner)

    visit(tree)
    return found


def reference_sites(sources: dict[str, str]) -> dict[str, list[str]]:
    """Name/attribute spellings and locations, not resolved lexical bindings."""
    sites: dict[str, list[str]] = {}
    for path, source in sorted(sources.items()):
        for node in ast.walk(ast.parse(source, filename=path)):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                name = node.id
            elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
                name = node.attr
            else:
                continue
            location = f"{path}:{node.lineno}"
            locations = sites.setdefault(name, [])
            if location not in locations:
                locations.append(location)
    return sites
