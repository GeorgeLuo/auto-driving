"""CLI modules talk through public names, import each other at module level, and form no cycle,
as modules or as the packages under ``automa_cli``."""

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

    return _imports_of(path, path.read_text(encoding="utf-8"), _module_name(path), path.name == "__init__.py")


def _imports_of(path: Path, source: str, module: str, init: bool) -> list[CliImport]:
    """Imports in ``source``, as if it were the CLI module ``module``.

    ``from . import render as presentation`` keeps the imported name ``render``,
    so the alias does not hide the target.
    """

    package = (module if init else module.rpartition(".")[0]).split(".")
    found: list[CliImport] = []

    def visit(node: ast.AST, in_function: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ImportFrom):
                base = package[:len(package) - child.level + 1] if child.level else []
                imported = ".".join([*base, *([child.module] if child.module else [])])
                if imported == PACKAGE or imported.startswith(f"{PACKAGE}."):
                    names = tuple(alias.name for alias in child.names)
                    found.append(CliImport(path, child.lineno, imported, names, in_function))
            elif isinstance(child, ast.Import):
                found.extend(
                    CliImport(path, child.lineno, alias.name, (), in_function)
                    for alias in child.names if alias.name.startswith(f"{PACKAGE}.")
                )
            visit(child, in_function or isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)))

    visit(ast.parse(source, filename=str(path)), False)
    return found


def _import_edges(imports: dict[str, list[CliImport]], modules: set[str]) -> dict[str, dict[str, CliImport]]:
    """Each module's module-level imports of other CLI modules, by the first import of each."""

    edges: dict[str, dict[str, CliImport]] = {module: {} for module in modules}
    for module, found_imports in imports.items():
        edges.setdefault(module, {})
        for found in found_imports:
            if found.in_function:
                continue
            for target in (found.module, *(f"{found.module}.{name}" for name in found.names)):
                if target in modules and target != module:
                    edges[module].setdefault(target, found)
    return edges


# Files with these names have that responsibility. Anything else in a feature is support.
_LAYER_NAMES = {"commands", "service", "models", "render", "store", "worker", "monitor", "registry"}
_OPERATION_ROLES = {"service", "support", "store", "worker", "monitor", "registry"}
# Features split by responsibility. Later slices add to this tuple.
LAYERED_FEATURES = (
    f"{PACKAGE}.plugins.catalog",
    f"{PACKAGE}.plugins.inspect",
    f"{PACKAGE}.plugins.stream",
    f"{PACKAGE}.hosts.runs",
)
LAYERED_ASSEMBLERS = (
    f"{PACKAGE}.plugins.commands",
    f"{PACKAGE}.hosts.commands",
)


def _role(module: str) -> str:
    leaf = module.rsplit(".", 1)[-1]
    return leaf if leaf in _LAYER_NAMES else "support"


def _feature_package(module: str) -> str:
    return module.rsplit(".", 1)[0]


def _layered(module: str) -> bool:
    if module in LAYERED_ASSEMBLERS:
        return True
    return any(module == feature or module.startswith(feature + ".") for feature in LAYERED_FEATURES)


def _layer_reason(source: str, target: str) -> str | None:
    """Why ``source`` may not import ``target``, or None when the edge is allowed.

    A parent command module may import a child command module to register it.
    This is an import relationship, not a proof that a renderer avoids I/O.
    """

    source_role = _role(source)
    target_role = _role(target)
    same_feature = _feature_package(source) == _feature_package(target)
    # The run worker is the process entry for hosts run. It composes the
    # renderer for its own stdout and passes that object into the service.
    if source_role == "worker" and target_role == "render" and same_feature:
        return None
    if source_role in _OPERATION_ROLES and target_role in {"commands", "render"}:
        return "operation imports presentation"
    if source_role == "models" and target_role in {"commands", "render", "service", "store", "worker", "monitor", "registry"}:
        return "model imports an operation or presentation"
    if source_role == "models" and target_role == "support" and same_feature:
        return "model imports an operation"
    if source_role == "render" and target_role in {"commands", "service", "store", "worker", "monitor", "registry"}:
        return "renderer imports an operation"
    if source_role == "render" and target_role == "support" and same_feature:
        return "renderer imports an operation"
    if source_role == "render" and target.rsplit(".", 1)[-1] in {"view_feed", "session"}:
        return "renderer imports a viewer"
    if source_role == "commands" and target_role == "commands":
        source_package = _feature_package(source)
        if target.startswith(source_package + ".") and target != source:
            return None
        return "command handler reused outside its assembler"
    return None


def _layer_violations(edges: dict[str, dict[str, CliImport]]) -> list[str]:
    violations = []
    for source, targets in edges.items():
        if not _layered(source):
            continue
        for target, found in targets.items():
            reason = _layer_reason(source, target)
            if reason is not None:
                violations.append(f"{found.where} {reason}: {target}")
    return violations


def _fixture_edges(files: dict[str, str]) -> dict[str, dict[str, CliImport]]:
    imports = {
        module: _imports_of(ROOT / Path(*module.split(".")).with_suffix(".py"), source, module, False)
        for module, source in files.items()
    }
    return _import_edges(imports, set(files))


def _package(module: str) -> str:
    """The name under ``automa_cli`` that holds ``module``: a command package, ``shared``,
    ``frontend``, or a top-level module such as ``app``."""

    return module[len(PACKAGE) + 1:].partition(".")[0] or PACKAGE


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

    def _edges(self) -> dict[str, dict[str, CliImport]]:
        """Each module's module-level imports of other CLI modules, by the first import of each."""

        return _import_edges(self.imports, self.modules)

    def test_module_imports_form_no_cycle(self) -> None:
        edges = self._edges()
        violations = [
            f"cycle among {', '.join(group)}\n" + "\n".join(
                f"  {edges[module][target].where} imports {target}"
                for module in group for target in sorted(edges[module]) if target in group
            )
            for group in _cycles({module: set(targets) for module, targets in edges.items()})
        ]
        self.assertEqual(violations, [])

    def test_host_subpackages_form_no_cycle(self) -> None:
        """Host subsystems stay independently navigable as their modules move."""
        prefix = f"{PACKAGE}.hosts."
        groups = {"deployment", "runs", "publications", "views"}
        graph: dict[str, set[str]] = {group: set() for group in groups}
        for module, targets in self._edges().items():
            if not module.startswith(prefix):
                continue
            group = module[len(prefix):].partition(".")[0]
            if group not in groups:
                continue
            for target in targets:
                if target.startswith(prefix):
                    target_group = target[len(prefix):].partition(".")[0]
                    if target_group in groups and target_group != group:
                        graph[group].add(target_group)
        self.assertEqual(_cycles(graph), [])

    def test_package_imports_form_no_cycle(self) -> None:
        imports: dict[str, list[tuple[str, CliImport]]] = {_package(module): [] for module in self.modules}
        graph: dict[str, set[str]] = {package: set() for package in imports}
        for module, targets in self._edges().items():
            for target, found in targets.items():
                if _package(target) != _package(module):
                    graph[_package(module)].add(_package(target))
                    imports[_package(module)].append((target, found))
        violations = [
            f"cycle among packages {', '.join(group)}\n" + "\n".join(
                f"  {found.where} imports {target}"
                for package in group for target, found in imports[package] if _package(target) in group
            )
            for group in _cycles(graph)
        ]
        self.assertEqual(violations, [])

    def test_plugin_layers_keep_operations_from_presentation(self) -> None:
        """Layered features keep commands, services, and rendering apart.

        The check resolves relative imports and aliases. It does not prove a
        renderer performs no I/O; stream and host-status tests render from
        supplied data.
        """

        self.assertEqual(_layer_violations(self._edges()), [])

    def test_layer_fixtures_accept_assembly_and_reject_crossed_imports(self) -> None:
        allowed = {
            f"{PACKAGE}.plugins.commands": "from .catalog.commands import register_catalog\n",
            f"{PACKAGE}.plugins.catalog.commands": "from .service import run\nfrom .render import show\n",
            f"{PACKAGE}.plugins.catalog.service": "from .models import Result\n",
            f"{PACKAGE}.plugins.catalog.render": "from .models import Result\n",
            f"{PACKAGE}.plugins.catalog.models": "",
            f"{PACKAGE}.hosts.commands": "from .runs.commands import register_host_runs\n",
            f"{PACKAGE}.hosts.runs.commands": "",
            f"{PACKAGE}.hosts.runs.worker": "from .render import RunOutput\n",
            f"{PACKAGE}.hosts.runs.render": "",
        }
        self.assertEqual(_layer_violations(_fixture_edges(allowed)), [])

        crossed = {
            f"{PACKAGE}.plugins.catalog.service": "from . import render as presentation\n",
            f"{PACKAGE}.plugins.catalog.render": "",
            f"{PACKAGE}.plugins.catalog.commands": "from ..inspect.commands import register_inspect\n",
            f"{PACKAGE}.plugins.inspect.commands": "",
            f"{PACKAGE}.hosts.runs.service": "from .render import RunOutput\n",
            f"{PACKAGE}.hosts.runs.render": "",
            f"{PACKAGE}.hosts.runs.commands": "from ..deployment.commands import register_host_deployment\n",
            f"{PACKAGE}.hosts.deployment.commands": "",
        }
        violations = _layer_violations(_fixture_edges(crossed))
        self.assertEqual(sorted(violations), sorted([
            "cli/automa_cli/plugins/catalog/commands.py:1 command handler reused outside its assembler: "
            f"{PACKAGE}.plugins.inspect.commands",
            "cli/automa_cli/plugins/catalog/service.py:1 operation imports presentation: "
            f"{PACKAGE}.plugins.catalog.render",
            "cli/automa_cli/hosts/runs/service.py:1 operation imports presentation: "
            f"{PACKAGE}.hosts.runs.render",
            "cli/automa_cli/hosts/runs/commands.py:1 command handler reused outside its assembler: "
            f"{PACKAGE}.hosts.deployment.commands",
        ]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
