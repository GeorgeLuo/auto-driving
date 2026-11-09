"""Independent stub and unreachable-code indicators."""

from __future__ import annotations

import ast
from collections.abc import Mapping
from typing import Any

from .context import AnalysisContext
from .report import kind_finding, measured_factor as _factor


FACTOR = "functionality"
INDICATOR_ID = "structure.functionality"
INDICATOR_VERSION = "2"
DETECTOR_IDS = {
    "stub": f"{INDICATOR_ID}.stub",
    "unreachable": f"{INDICATOR_ID}.unreachable",
}


def analyze(context: AnalysisContext) -> dict[str, Any]:
    """Measure stubs and obvious same-body unreachable statements."""

    trees = {
        path: tree
        for path, tree in context.trees(family="default").items()
        if path.endswith(".py")
    }
    return _analyze_trees(trees, context=context)


def _analyze_trees(
    trees: Mapping[str, ast.Module], *, context: AnalysisContext | None = None
) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    stub_count = 0
    unreachable_count = 0

    for path in sorted(trees):
        tree = trees[path]
        declarations = _contract_declarations(tree)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node not in declarations and stub(node):
                stub_count += 1
                findings.append(
                    _finding(
                        context,
                        {
                            "path": path,
                            "line": int(node.lineno),
                            "kind": "stub",
                            "message": f"Stub callable observed: {node.name}.",
                            "name": node.name,
                        },
                        ((path, node),),
                    )
                )

        unreachable_count += _collect_unreachable(
            path, tree.body, findings, context=context
        )
        for node in ast.walk(tree):
            if node is tree:
                continue
            for attr in ("body", "orelse", "finalbody"):
                block = getattr(node, attr, None)
                if isinstance(block, list) and block and isinstance(block[0], ast.stmt):
                    unreachable_count += _collect_unreachable(
                        path, block, findings, context=context
                    )

    return _factor(
        {
            "stub_count": stub_count,
            "unreachable_count": unreachable_count,
        },
        findings,
        ["Only module-level classes with direct protocol bases or explicit abstract decorators from unshadowed imports are recognized; inspect other intentional hooks before removing code."],
    )


def _contract_declarations(tree: ast.Module) -> set[ast.AST]:
    """Identify directly declared protocol methods and explicitly abstract methods."""
    protocols, typing_modules = _imported_names(tree, {"typing", "typing_extensions"}, "Protocol")
    abstracts, abc_modules = _imported_names(tree, {"abc"}, "abstractmethod")
    declarations: set[ast.AST] = set()
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        methods = [child for child in node.body if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))]
        if any(
            _imported_symbol(base.value if isinstance(base, ast.Subscript) else base,
                            protocols, typing_modules, "Protocol") for base in node.bases
        ):
            declarations.update(methods)
        for method in methods:
            if any(_imported_symbol(decorator, abstracts, abc_modules, "abstractmethod")
                   for decorator in method.decorator_list):
                declarations.add(method)
    return declarations


def _imported_names(tree: ast.Module, modules: set[str], name: str) -> tuple[set[str], set[str]]:
    direct, qualified, shadowed = set(), set(), set()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            bindings = _import_bindings(node, modules, name)
            if bindings is None:
                return set(), set()
            imported, module_names, other_names = bindings
            direct.update(imported)
            qualified.update(module_names)
            shadowed.update(other_names)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            shadowed.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            shadowed.update(child.id for child in ast.walk(node) if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store))
    return direct - shadowed, qualified - shadowed


def _import_bindings(node: ast.Import | ast.ImportFrom, modules: set[str], name: str) -> tuple[set[str], set[str], set[str]] | None:
    direct, qualified, other = set(), set(), set()
    for alias in node.names:
        if alias.name == "*":
            return None
        if isinstance(node, ast.ImportFrom):
            binding = alias.asname or alias.name
            target = direct if node.module in modules and node.level == 0 and alias.name == name else other
        else:
            binding = alias.asname or alias.name.split(".")[0]
            target = qualified if alias.name in modules else other
        target.add(binding)
    return direct, qualified, other


def _imported_symbol(node: ast.AST, direct: set[str], qualified: set[str], name: str) -> bool:
    if isinstance(node, ast.Name):
        return node.id in direct
    return (isinstance(node, ast.Attribute) and node.attr == name
            and isinstance(node.value, ast.Name) and node.value.id in qualified)


def stub(node: ast.AST) -> bool:
    """Return whether a callable contains one of the legacy stub forms."""

    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    if len(node.body) != 1:
        return False
    stmt = node.body[0]
    if isinstance(stmt, ast.Pass):
        return True
    if (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and stmt.value.value is ...
    ):
        return True
    if isinstance(stmt, ast.Raise) and stmt.exc is not None:
        exc = stmt.exc
        if isinstance(exc, ast.Name) and exc.id in {"NotImplementedError", "NotImplemented"}:
            return True
        if isinstance(exc, ast.Call) and isinstance(exc.func, ast.Name):
            if exc.func.id in {"NotImplementedError", "NotImplemented"}:
                return True
    return False


def is_terminal(node: ast.AST) -> bool:
    """Return whether a statement terminates the current body scan."""

    return isinstance(node, (ast.Return, ast.Raise))


def collect_unreachable(body: list[ast.stmt]) -> tuple[ast.stmt, ...]:
    """Return statements after the first return/raise in one body list."""

    unreachable: list[ast.stmt] = []
    seen_terminal = False
    for stmt in body:
        if seen_terminal:
            unreachable.append(stmt)
            continue
        if is_terminal(stmt):
            seen_terminal = True
    return tuple(unreachable)


def unreachable(body: list[ast.stmt]) -> bool:
    """Return whether a body contains at least one obviously unreachable statement."""

    return bool(collect_unreachable(body))


def _collect_unreachable(
    path: str,
    body: list[ast.stmt],
    findings: list[dict[str, Any]],
    *,
    context: AnalysisContext | None = None,
) -> int:
    statements = collect_unreachable(body)
    for stmt in statements:
        findings.append(
            _finding(
                context,
                {
                    "path": path,
                    "line": int(stmt.lineno),
                    "kind": "unreachable",
                    "message": (
                        "Statement appears after a terminal return or raise "
                        "in the same body list."
                    ),
                },
                ((path, stmt),),
            )
        )
    return len(statements)


def _finding(
    context: AnalysisContext | None,
    finding: dict[str, Any],
    node_refs: tuple[tuple[str, ast.AST], ...],
) -> dict[str, Any]:
    path, node = node_refs[0]
    return kind_finding(
        context,
        finding,
        detector_ids=DETECTOR_IDS,
        indicator_version=INDICATOR_VERSION,
        path=path,
        node=node,
        inspection_question=(
            "Inspect intentional hooks, protocols, and control-flow intent before changing this code."
        ),
    )


__all__ = [
    "DETECTOR_IDS",
    "FACTOR",
    "INDICATOR_ID",
    "INDICATOR_VERSION",
    "analyze",
    "collect_unreachable",
    "is_terminal",
    "stub",
    "unreachable",
]
