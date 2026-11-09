"""Independent stub and unreachable-code indicators."""

from __future__ import annotations

import ast
from collections.abc import Mapping
from dataclasses import dataclass
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
        for node in _stub_candidates(tree):
            if stub(node):
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


def _stub_candidates(tree: ast.Module) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Functions and methods expected to have a body: all but protocol and abstract declarations."""

    declarations = _contract_declarations(tree)
    callables = (node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)))
    return [node for node in callables if node not in declarations]


def _contract_declarations(tree: ast.Module) -> set[ast.AST]:
    """Methods of module-level protocol classes, and methods marked abstract."""

    protocol = _imported_name(tree, {"typing", "typing_extensions"}, "Protocol")
    abstract = _imported_name(tree, {"abc"}, "abstractmethod")
    declarations: set[ast.AST] = set()
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        methods = [child for child in node.body if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))]
        if any(protocol.matches(base) for base in node.bases):
            declarations.update(methods)
        declarations.update(
            method for method in methods if any(abstract.matches(decorator) for decorator in method.decorator_list)
        )
    return declarations


@dataclass(frozen=True)
class _ImportedName:
    """The local spellings of ``module.name``: bound directly, or through its module."""

    name: str
    direct: frozenset[str] = frozenset()
    modules: frozenset[str] = frozenset()

    def matches(self, node: ast.AST) -> bool:
        if isinstance(node, ast.Subscript):
            node = node.value  # A generic base such as Protocol[T].
        if isinstance(node, ast.Name):
            return node.id in self.direct
        if not isinstance(node, ast.Attribute) or node.attr != self.name:
            return False
        return isinstance(node.value, ast.Name) and node.value.id in self.modules


def _imported_name(tree: ast.Module, modules: set[str], name: str) -> _ImportedName:
    """How module-level code refers to ``name`` from ``modules``, ignoring shadowed bindings.

    A star import could rebind anything, so it leaves the name unrecognized.
    """

    direct, qualified, shadowed = set(), set(), set()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            bindings = _import_bindings(node, modules, name)
            if bindings is None:
                return _ImportedName(name)
            imported, module_names, other_names = bindings
            direct.update(imported)
            qualified.update(module_names)
            shadowed.update(other_names)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            shadowed.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            shadowed.update(child.id for child in ast.walk(node) if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store))
    return _ImportedName(name, frozenset(direct - shadowed), frozenset(qualified - shadowed))


def _import_bindings(node: ast.Import | ast.ImportFrom, modules: set[str], name: str) -> tuple[set[str], set[str], set[str]] | None:
    """Split an import's bindings into ``name`` itself, the modules, and anything else; None for a star import."""

    if any(alias.name == "*" for alias in node.names):
        return None
    if isinstance(node, ast.Import):
        bound = [(alias.asname or alias.name.split(".")[0], alias.name in modules) for alias in node.names]
        return set(), {binding for binding, module in bound if module}, {binding for binding, module in bound if not module}
    # Only an absolute import from one of ``modules`` binds ``name`` itself.
    from_modules = node.level == 0 and node.module in modules
    bound = [(alias.asname or alias.name, from_modules and alias.name == name) for alias in node.names]
    return {binding for binding, own in bound if own}, set(), {binding for binding, own in bound if not own}


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
