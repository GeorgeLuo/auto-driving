"""Review candidates beyond lint: unused production code and related functions.

Both read every production source under the paths they are given, so they run
with project reach.  Their findings are review questions, not removal or
consolidation verdicts: Vulture resolves names, not bindings, and related
functions are ranked by the calls they share, not by proven equivalence.
"""

from __future__ import annotations

import ast
import builtins
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from vulture import Vulture
from vulture.core import Item

from qca.analyzer import classify_source
from qca.evaluation import Finding, definitions, finding, location

UNUSED_KINDS = frozenset({"attribute", "class", "function", "method", "property"})
SHOWN_TEST_REFERENCES = 5
RELATED_LIMIT = 3
# Below about a quarter of their weighted calls in common, matches mostly share generic helpers.
RELATED_MIN_SCORE = 0.25


@dataclass(frozen=True)
class Callable:
    path: str
    name: str
    line: int
    end_line: int
    calls: frozenset[str]


def unused(tree: Path, paths: list[str]) -> list[Finding]:
    """Production definitions no production code refers to by name."""

    sources = _sources(tree, paths)
    production = {path: source for path, source in sources.items() if not _is_test(path, source)}
    references = _test_references({path: source for path, source in sources.items() if path not in production})
    scanner = Vulture()
    for path, source in sorted(production.items()):
        scanner.scan(source, filename=path)
    scanner.scavenge([])  # Applies Vulture's bundled allowances, such as AST visitor methods.
    owners = {path: definitions(_parse(source)) for path, source in production.items()}
    findings = []
    for item in scanner.get_unused_code():
        path = str(item.filename)
        if item.typ not in UNUSED_KINDS or path not in owners:
            continue
        name = _qualified_name(item, owners[path])
        referenced = references.get(item.name, [])
        # Located at the definition line, so editing an already unused body is not a new finding.
        findings.append(finding(
            "vulture",
            f"unused-{item.typ}",
            location(path, item.first_lineno, symbol=name),
            f"Is this {item.typ} still needed? No production code refers to {item.name!r}.",
            evidence={
                "confidence": item.confidence,
                "test_reference_count": len(referenced),
                "test_references": referenced[:SHOWN_TEST_REFERENCES],
            },
        ))
    return findings


def related(tree: Path, paths: list[str]) -> list[Finding]:
    """For each function, the most similar functions it shares calls with nearby."""

    production = {path: source for path, source in _sources(tree, paths).items() if not _is_test(path, source)}
    functions = [function for path, source in sorted(production.items()) for function in _callables(path, source)]
    neighbors = _import_neighbors(production)
    frequencies = Counter(call for function in functions for call in function.calls)
    weights = {call: math.log((1 + len(functions)) / (1 + count)) + 1 for call, count in frequencies.items()}
    callers: defaultdict[str, set[int]] = defaultdict(set)
    for index, function in enumerate(functions):
        for call in function.calls:
            callers[call].add(index)
    findings = []
    for function in functions:
        candidates = {functions[index] for call in function.calls for index in callers[call]}
        matches = _rank(function, candidates, neighbors, weights)
        if not matches:
            continue
        names = ", ".join(other.name for other, *_ in matches)
        # Located across the whole function, so any edit to it raises the comparison again.
        findings.append(finding(
            "inspection",
            "related-functions",
            location(function.path, function.line, function.end_line, function.name),
            f"Should this function and {names} share code, or at least change together?",
            evidence={"matches": [
                {"symbol": other.name, "shared_calls": list(shared), "score": score, "relationship": relationship}
                for other, shared, score, relationship in matches
            ]},
            related=[location(other.path, other.line, other.end_line, other.name) for other, *_ in matches],
        ))
    return findings


def _rank(
    function: Callable,
    candidates: set[Callable],
    neighbors: dict[str, set[str]],
    weights: dict[str, float],
) -> list[tuple[Callable, tuple[str, ...], float, str]]:
    """Up to ``RELATED_LIMIT`` nearby functions sharing two or more calls, by weighted overlap."""

    ranked = []
    for other in candidates - {function}:
        shared = function.calls & other.calls
        if len(shared) < 2:
            continue
        relationship = _relationship(function, other, neighbors)
        overlap = sum(weights[call] for call in shared) / sum(weights[call] for call in function.calls | other.calls)
        if relationship and overlap >= RELATED_MIN_SCORE:
            ranked.append((other, tuple(sorted(shared)), round(overlap, 4), relationship))
    ranked.sort(key=lambda match: (-match[2], match[0].path, match[0].name))
    return ranked[:RELATED_LIMIT]


def _relationship(function: Callable, other: Callable, neighbors: dict[str, set[str]]) -> str:
    if function.path == other.path:
        outer, inner = sorted((function.name, other.name), key=len)
        # A nested function is part of its parent, not a sibling.
        return "" if inner.startswith(f"{outer}.") else "same module"
    if other.path in neighbors.get(function.path, set()):
        return "import neighbor"
    if PurePosixPath(function.path).parent == PurePosixPath(other.path).parent:
        return "same directory"
    return ""


def _sources(tree: Path, paths: list[str]) -> dict[str, str]:
    """Python sources under ``paths`` that the repository maintains, not vendored or generated."""

    found = {}
    for root in paths:
        for file in sorted((tree / root).rglob("*.py")) if (tree / root).is_dir() else [tree / root]:
            relative = file.relative_to(tree).as_posix()
            source = file.read_text(encoding="utf-8", errors="replace")
            if file.suffix == ".py" and classify_source(relative, source) in {"production", "tooling/scripts", "tests"}:
                found[relative] = source
    return found


def _is_test(path: str, source: str) -> bool:
    return classify_source(path, source) == "tests"


def _test_references(tests: dict[str, str]) -> dict[str, list[str]]:
    """Where tests load each name or attribute; spellings, not resolved bindings."""

    sites: defaultdict[str, dict[str, None]] = defaultdict(dict)  # Ordered sets of "path:line".
    for path, source in sorted(tests.items()):
        for node in ast.walk(_parse(source)):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                sites[node.id][f"{path}:{node.lineno}"] = None
            elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
                sites[node.attr][f"{path}:{node.lineno}"] = None
    return {name: list(found) for name, found in sites.items()}


def _qualified_name(item: Item, owners: list[tuple[ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef, str]]) -> str:
    """An attribute is named within its class; other definitions by their own qualified name."""

    if item.typ == "attribute":
        owners = [(node, name) for node, name in owners if isinstance(node, ast.ClassDef)]
    enclosing = [name for node, name in owners if _first_line(node) <= item.first_lineno <= node.end_lineno]
    if not enclosing:
        return item.name
    return f"{enclosing[-1]}.{item.name}" if item.typ == "attribute" else enclosing[-1]


def _first_line(node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """Where a definition starts, counting its decorators as Vulture does."""

    return min([node.lineno, *(decorator.lineno for decorator in node.decorator_list)])


def _callables(path: str, source: str) -> list[Callable]:
    return [
        Callable(path, name, node.lineno, node.end_lineno or node.lineno, _direct_calls(node))
        for node, name in definitions(_parse(source))
        if not isinstance(node, ast.ClassDef)
    ]


def _direct_calls(function: ast.FunctionDef | ast.AsyncFunctionDef) -> frozenset[str]:
    """Spellings of non-builtin calls in the body, excluding nested definitions."""

    calls = set()
    pending: list[ast.AST] = list(function.body)
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        if isinstance(node, ast.Call):
            name = _spelling(node.func)
            if name and name not in vars(builtins):
                calls.add(name)
        pending.extend(ast.iter_child_nodes(node))
    return frozenset(calls)


def _spelling(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _spelling(node.value)
        return f"{parent}.{node.attr}" if parent else ""
    return ""


def _import_neighbors(sources: dict[str, str]) -> dict[str, set[str]]:
    """Files that import each other, in either direction, by absolute or relative import."""

    neighbors: defaultdict[str, set[str]] = defaultdict(set)
    for path, source in sources.items():
        for module in _imported_modules(path, _parse(source)):
            for target in (_module_files(module) & sources.keys()) - {path}:
                neighbors[path].add(target)
                neighbors[target].add(path)
    return neighbors


def _imported_modules(path: str, tree: ast.AST) -> set[str]:
    package = PurePosixPath(path).parent.parts
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = package[:len(package) - node.level + 1] if node.level else ()
            parent = ".".join([*base, *([node.module] if node.module else [])])
            modules.add(parent)
            modules.update(f"{parent}.{alias.name}" for alias in node.names)
    return modules


def _module_files(module: str) -> set[str]:
    path = module.replace(".", "/")
    return {f"{path}.py", f"{path}/__init__.py"}


def _parse(source: str) -> ast.AST:
    try:
        return ast.parse(source)
    except (SyntaxError, ValueError):
        return ast.Module(body=[], type_ignores=[])  # Unparseable sources are reported by Ruff.
