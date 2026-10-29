"""CLI modules talk through public names, import each other at module level, and form no cycle."""

from __future__ import annotations

import ast
import unittest
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
PACKAGE = "cli.automa_cli"


@dataclass(frozen=True)
class CliImport:
    path: Path
    line: int
    module: str
    names: tuple[str, ...]
    in_function: bool

    @property
    def where(self) -> str:
        return f"{self.path.relative_to(ROOT)}:{self.line}"


def _module_name(path: Path) -> str:
    parts = path.relative_to(ROOT).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _cli_imports(path: Path) -> list[CliImport]:
    """Each import of a CLI module in ``path``, with relative imports resolved."""

    name = _module_name(path)
    package = (name if path.name == "__init__.py" else name.rpartition(".")[0]).split(".")
    found: list[CliImport] = []

    def visit(node: ast.AST, in_function: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ImportFrom):
                base = package[:len(package) - child.level + 1] if child.level else []
                module = ".".join([*base, *([child.module] if child.module else [])])
                if module == PACKAGE or module.startswith(f"{PACKAGE}."):
                    names = tuple(alias.name for alias in child.names)
                    found.append(CliImport(path, child.lineno, module, names, in_function))
            elif isinstance(child, ast.Import):
                found.extend(
                    CliImport(path, child.lineno, alias.name, (), in_function)
                    for alias in child.names if alias.name.startswith(f"{PACKAGE}.")
                )
            visit(child, in_function or isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)))

    visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)), False)
    return found


def _cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    """Each group of modules that import one another (Tarjan's strongly connected components)."""

    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    cycles: list[list[str]] = []

    def connect(module: str) -> None:
        index[module] = low[module] = len(index)
        stack.append(module)
        for target in sorted(graph[module]):
            if target not in index:
                connect(target)
                low[module] = min(low[module], low[target])
            elif target in stack:
                low[module] = min(low[module], index[target])
        if low[module] == index[module]:
            group = []
            while not group or group[-1] != module:
                group.append(stack.pop())
            if len(group) > 1:
                cycles.append(sorted(group))

    for module in sorted(graph):
        if module not in index:
            connect(module)
    return cycles


class ModuleBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        paths = sorted((ROOT / "cli" / "automa_cli").rglob("*.py"))
        cls.modules = {_module_name(path) for path in paths}
        cls.imports = {_module_name(path): _cli_imports(path) for path in paths}

    def test_no_module_imports_another_modules_private_names(self) -> None:
        violations = [
            f"{found.where} imports {name} from {found.module}"
            for imports in self.imports.values() for found in imports for name in found.names
            if name.startswith("_") and not name.startswith("__")
        ]
        self.assertEqual(violations, [])

    def test_cli_modules_are_imported_at_module_level(self) -> None:
        violations = [
            f"{found.where} imports {found.module} inside a function"
            for imports in self.imports.values() for found in imports if found.in_function
        ]
        self.assertEqual(violations, [])

    def test_module_imports_form_no_cycle(self) -> None:
        edges: dict[str, dict[str, CliImport]] = {module: {} for module in self.modules}
        for module, imports in self.imports.items():
            for found in imports:
                if found.in_function:
                    continue
                for target in (found.module, *(f"{found.module}.{name}" for name in found.names)):
                    if target in self.modules and target != module:
                        edges[module].setdefault(target, found)
        violations = [
            f"cycle among {', '.join(group)}\n" + "\n".join(
                f"  {edges[module][target].where} imports {target}"
                for module in group for target in sorted(edges[module]) if target in group
            )
            for group in _cycles({module: set(targets) for module, targets in edges.items()})
        ]
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
