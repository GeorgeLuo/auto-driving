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


# Conventional filenames declare a role. Other modules have an explicit role;
# a new filename must be classified here, never silently treated as support.
_NAMED_ROLES = {name: name for name in ("commands", "service", "models", "render", "store", "monitor", "registry")}
_OPERATION_ROLES = {"service", "store", "monitor", "registry", "operation"}
_MODULE_ROLES = {
    "app": "entrypoint",
    "frontend.decision_view": "operation",
    "frontend.loopback_http": "operation",
    "frontend.perception_view": "operation",
    "frontend.plugin_catalog": "operation",
    "frontend.server": "operation",
    "frontend.workbench_contract": "pure",
    "frontend.workbench_view": "operation",
    "hosts.bundles": "store",
    "hosts.deployment.donkeycar_vendor": "operation",
    "hosts.deployment.picar_deploy": "operation",
    "hosts.discovery": "operation",
    "hosts.host_formatting": "pure",
    "hosts.host_status": "operation",
    "hosts.local": "operation",
    "hosts.local_worker": "entrypoint",
    "hosts.publications.decision": "operation",
    "hosts.publications.decision_publication": "pure",
    "hosts.publications.decision_records": "operation",
    "hosts.publications.host_publications": "operation",
    "hosts.publications.host_telemetry": "pure",
    "hosts.publications.plugin_catalog": "operation",
    "hosts.publications.publication_fields": "pure",
    "hosts.runs.process": "operation",
    "hosts.runs.status": "operation",
    "hosts.runs.worker": "entrypoint",
    "hosts.staging": "store",
    "hosts.step_activations": "store",
    "hosts.step_formatting": "pure",
    "hosts.vehicle_access": "operation",
    "hosts.views.decision_live": "operation",
    "hosts.views.polling": "pure",
    "hosts.views.runtime_view": "operation",
    "hosts.views.view_discovery": "operation",
    "hosts.views.view_feed": "operation",
    "hosts.views.viewer": "operation",
    "plugins.inspect.replay": "operation",
    "plugins.step_formatting": "pure",
    "plugins.stream.decision": "operation",
    "plugins.stream.memory": "operation",
    "plugins.stream.perception": "operation",
    "plugins.stream.polling": "pure",
    "plugins.stream.session": "operation",
    "providers.catalog": "registry",
    "providers.metadata": "pure",
    "providers.probes": "operation",
    "providers.urls": "pure",
    "releases.errors": "pure",
    "releases.paths": "pure",
    "replay.frames": "operation",
    "replay.selections": "operation",
    "replay.source": "store",
    "shared.browser_result": "models",
    "shared.cli.selection_args": "cli",
    "shared.cli.timeout_input": "cli",
    "shared.command_result": "models",
    "shared.io.http_json": "operation",
    "shared.io.json_files": "store",
    "shared.io.processes": "operation",
    "shared.memory_report": "pure",
    "shared.open_view": "operation",
    "shared.paths": "pure",
    "shared.plugin_catalog": "pure",
    "shared.progress": "pure",
    "shared.step_hosting": "pure",
    "shared.text": "pure",
    "shared.timestamps": "pure",
    "simulators.process": "operation",
    "vehicles.discovery": "operation",
    "vehicles.status": "operation",
    "workbench.plugins": "operation",
    "workbench.presentation": "pure",
    "workbench.replay": "operation",
    "workbench.runner": "operation",
}
_PACKAGE_MODULES = {_module_name(path) for path in (ROOT / "cli" / "automa_cli").rglob("__init__.py")}
_ASSEMBLERS = {f"{PACKAGE}.{name}" for name in ("hosts.commands", "plugins.commands")}


def _role(module: str) -> str | None:
    if module in _PACKAGE_MODULES:
        return "package"
    relative = module.removeprefix(PACKAGE + ".")
    return _MODULE_ROLES.get(relative, _NAMED_ROLES.get(module.rsplit(".", 1)[-1]))


def _feature_package(module: str) -> str:
    return module.rsplit(".", 1)[0]


def _layer_reason(source: str, target: str) -> str | None:
    """Check every intra-CLI edge, including concrete support filenames."""

    if source.startswith(f"{PACKAGE}.shared.") and not target.startswith(f"{PACKAGE}.shared."):
        return "shared imports a feature"
    if source.startswith(f"{PACKAGE}.providers.") and target.startswith(f"{PACKAGE}.hosts.deployment."):
        return "provider imports deployment"
    if source in {f"{PACKAGE}.hosts.runs.service", f"{PACKAGE}.hosts.runs.process"} and target == f"{PACKAGE}.shared.io.json_files":
        return "run records belong to store"
    source_role, target_role = _role(source), _role(target)
    if source_role is None or target_role is None:
        return "unclassified module"
    if source_role == "package":
        return "package initializer imports implementation"
    if source_role in _OPERATION_ROLES and target_role in {"commands", "render", "entrypoint", "cli"}:
        return "operation imports presentation"
    if source_role in {"models", "pure", "render"} and target_role in _OPERATION_ROLES | {"commands", "entrypoint", "cli"}:
        return "data or rendering imports operations"
    if source_role in {"models", "pure"} and target_role == "render":
        return "data imports presentation"
    if target_role == "commands":
        if source == f"{PACKAGE}.app":
            return None
        if source in _ASSEMBLERS and target.startswith(_feature_package(source) + "."):
            return None
        return "command handler reused outside its assembler"
    # Only the actual process entrypoints compose presentation and operations.
    if target_role == "entrypoint":
        return "process entrypoint reused as a library"
    return None


def _layer_violations(edges: dict[str, dict[str, CliImport]]) -> list[str]:
    return [
        f"{found.where} {reason}: {target}"
        for source, targets in edges.items() for target, found in targets.items()
        if (reason := _layer_reason(source, target)) is not None
    ]


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
        renderer performs no I/O; stream, host-status, deployment, vehicle
        status, and workbench tests render from supplied data. The run worker
        may import its own renderer because that process entry composes the
        transcript the service receives.
        """

        self.assertEqual(_layer_violations(self._edges()), [])

    def test_every_module_has_a_role_and_no_stale_assignments(self) -> None:
        self.assertEqual(sorted(module for module in self.modules if _role(module) is None), [])
        self.assertEqual(sorted(name for name in _MODULE_ROLES if f"{PACKAGE}.{name}" not in self.modules), [])
        self.assertIsNone(_role(f"{PACKAGE}.hosts.runs.future_helper"))

    def test_package_initializers_only_contain_metadata(self) -> None:
        for path in (ROOT / "cli" / "automa_cli").rglob("__init__.py"):
            body = ast.parse(path.read_text()).body
            statements = [node for node in body if not (
                isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)) and not (
                    isinstance(node, ast.Assign) and all(isinstance(t, ast.Name) and t.id in {"__all__", "__version__"} for t in node.targets)
                    and isinstance(node.value, (ast.Constant, ast.List, ast.Tuple)))]
            self.assertEqual(statements, [], str(path))

    def test_shared_and_external_host_operation_edges_are_rejected(self) -> None:
        files = {
            f"{PACKAGE}.shared.cli.example": "from ...plugins.catalog.commands import register_catalog",
            f"{PACKAGE}.plugins.catalog.commands": "",
            f"{PACKAGE}.plugins.stream.render": "from ...hosts.local import ensure_chase_host",
            f"{PACKAGE}.hosts.local": "",
        }
        violations = _layer_violations(_fixture_edges(files))
        self.assertEqual(len(violations), 2)
        self.assertTrue(any("shared imports a feature" in v for v in violations))
        self.assertTrue(any("imports operations" in v for v in violations))

    def test_only_output_adapters_print_or_accept_output_streams(self) -> None:
        violations = []
        for path in (ROOT / "cli" / "automa_cli").rglob("*.py"):
            role = _role(_module_name(path))
            if role in {"commands", "entrypoint", "cli"}:
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print":
                    violations.append(f"{path}:{node.lineno} prints")
                if role in {"render", "models", "pure"} and isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {"write", "write_text", "write_bytes", "flush"}:
                    violations.append(f"{path}:{node.lineno} writes")
                if isinstance(node, ast.arg) and isinstance(node.annotation, ast.AST) and "TextIO" in ast.unparse(node.annotation):
                    violations.append(f"{path}:{node.lineno} takes an output stream")
        self.assertEqual(violations, [])

    def test_concrete_operation_imports_and_unknown_modules_are_rejected(self) -> None:
        for target in ("providers.catalog", "providers.probes", "hosts.bundles", "workbench.runner",
                       "hosts.runs.future_helper"):
            for source in ("plugins.stream.render", "hosts.runs.models", "hosts.step_formatting"):
                with self.subTest(source=source, target=target):
                    self.assertIsNotNone(_layer_reason(f"{PACKAGE}.{source}", f"{PACKAGE}.{target}"))
        self.assertIsNotNone(_layer_reason(f"{PACKAGE}.providers.catalog", f"{PACKAGE}.hosts.deployment.service"))
        self.assertIsNotNone(_layer_reason(f"{PACKAGE}.hosts.runs.service", f"{PACKAGE}.shared.io.json_files"))
        self.assertIsNone(_layer_reason(f"{PACKAGE}.vehicles.render", f"{PACKAGE}.providers.metadata"))
        self.assertIsNone(_layer_reason(f"{PACKAGE}.hosts.runs.worker", f"{PACKAGE}.hosts.runs.render"))

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
