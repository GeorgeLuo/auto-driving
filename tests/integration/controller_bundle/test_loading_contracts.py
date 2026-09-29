"""Contracts for moving modules without splitting loaded classes.

Dynamic ``module:Class`` specs, package re-exports, and staged bundle prefixes
identify objects by path. These checks fail when a move drops a path, archives
a source file somewhere other than its current path, or makes a staged import
use a different class than the host or its sibling bundle copy.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import json
import sys
import tempfile
import unittest
from pathlib import Path

from autonomy.memory.activation import read_memory_activation
from cli.automa_cli.bundles import controller_bundle_paths, sync_controller_bundle
from cli.automa_cli.memory_runtime import _BUNDLE_PREFIXES as MEMORY_BUNDLE_PREFIXES
from cli.automa_cli.memory_runtime import load_memory_step_from_bundle
from cli.automa_cli.perception import _BUNDLE_PREFIXES as PERCEPTION_BUNDLE_PREFIXES
from cli.automa_cli.perception import _load_mapper
from cli.automa_cli.staged_bundle import StagedBundleImport
from implementations.memory import build_memory_activation_payload
from implementations.perception.catalog import PERCEPTION_MAPPER_SPEC, PERCEPTION_PLUGIN_SPECS


ROOT = Path(__file__).resolve().parents[3]
_SOURCE_ROOTS = (
    ROOT / "autonomy",
    ROOT / "implementations",
    ROOT / "cli",
    ROOT / "scripts",
    ROOT / "deploy" / "targets" / "donkeycar" / "app",
)
_SPEC_PREFIXES = ("autonomy.", "implementations.", "cli.")
_MODULE_PREFIXES = ("autonomy.", "implementations.", "lab.", "cli.")
_OWNED_PREFIXES = ("autonomy.", "implementations.", "cli.")


def _python_files(roots: tuple[Path, ...]) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts or "vendor" in path.parts:
                continue
            files.append(path)
    return files


def _absolute_module(path: Path, node: ast.ImportFrom) -> str | None:
    if node.level == 0:
        return node.module
    parts = path.relative_to(ROOT).parts[:-1]
    climb = node.level - 1
    if climb > len(parts):
        return None
    base = parts[: len(parts) - climb]
    if node.module:
        base += tuple(node.module.split("."))
    if not base:
        return None
    return ".".join(base)


def _path_literals() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for path in _python_files(_SOURCE_ROOTS):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        relative = path.relative_to(ROOT).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if node.value.startswith(_MODULE_PREFIXES):
                found.append((node.value, f"{relative}:{node.lineno}"))
    return found


def _is_import_spec(value: str) -> bool:
    module_name, separator, attribute = value.partition(":")
    if (
        not separator
        or ":" in attribute
        or not attribute.isidentifier()
        or not module_name.startswith(_SPEC_PREFIXES)
    ):
        return False
    return all(part.isidentifier() for part in module_name.split("."))


def _is_dotted_module_path(value: str) -> bool:
    if ":" in value or not value.startswith(_MODULE_PREFIXES):
        return False
    return all(part.isidentifier() for part in value.split("."))


def _load_spec(spec: str) -> object:
    module_name, separator, attribute = spec.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError(f"import spec must be 'module.path:name', got {spec!r}")
    return getattr(importlib.import_module(module_name), attribute)


def _load_dotted(value: str) -> object:
    try:
        return importlib.import_module(value)
    except ModuleNotFoundError:
        module_name, separator, attribute = value.rpartition(".")
        if not separator:
            raise
        return getattr(importlib.import_module(module_name), attribute)


def _bundle_source_paths() -> list[str]:
    paths: list[str] = []
    for package_root in ("autonomy", "implementations"):
        directory = ROOT / package_root
        for path in sorted(directory.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(directory)
            if (
                "__pycache__" in relative.parts
                or relative.suffix == ".pyc"
                or relative.name == ".DS_Store"
            ):
                continue
            paths.append((Path(package_root) / relative).as_posix())
    return paths


class LoadingContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary = tempfile.TemporaryDirectory()
        cls.bundle = controller_bundle_paths(Path(cls._temporary.name) / "vehicle")
        cls.release = sync_controller_bundle(cls.bundle, output=None)
        cls.bundle_root = Path(cls.bundle["root_dir"])

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temporary.cleanup()

    def test_dynamic_path_literals_resolve_to_one_object(self) -> None:
        unresolved: list[str] = []
        for value, location in _path_literals():
            if _is_import_spec(value):
                loaded = _load_spec(value)
            elif _is_dotted_module_path(value):
                loaded = _load_dotted(value)
            else:
                continue
            if isinstance(loaded, type) or inspect.isfunction(loaded):
                owner = importlib.import_module(loaded.__module__)
                defined = getattr(owner, loaded.__name__)
                if defined is not loaded:
                    unresolved.append(f"{location} {value} is not {loaded.__module__}.{loaded.__name__}")
            elif not inspect.ismodule(loaded):
                unresolved.append(f"{location} {value} did not resolve to a module, class, or function")
        self.assertEqual(unresolved, [])

    def test_package_exports_are_the_defining_objects(self) -> None:
        mismatches: list[str] = []
        for package_root in ("autonomy", "implementations"):
            for init in sorted((ROOT / package_root).rglob("__init__.py")):
                module_name = ".".join(init.parent.relative_to(ROOT).parts)
                module = importlib.import_module(module_name)
                exported = getattr(module, "__all__", ())
                for name in exported:
                    value = getattr(module, name)
                    if not isinstance(value, type) and not inspect.isfunction(value):
                        continue
                    defining = getattr(value, "__module__", "")
                    if not isinstance(defining, str) or not defining.startswith(_OWNED_PREFIXES):
                        continue
                    owner = importlib.import_module(defining)
                    defined_name = getattr(value, "__name__", name)
                    if not hasattr(owner, defined_name) or getattr(owner, defined_name) is not value:
                        mismatches.append(f"{module_name}.{name} -> {defining}.{defined_name}")
        shared_memory = importlib.import_module("autonomy.memory").SharedMemory
        host_memory = importlib.import_module("autonomy.shared_memory").SharedMemory
        self.assertIs(shared_memory, host_memory)
        self.assertEqual(mismatches, [])

    def test_production_imports_resolve(self) -> None:
        failures: list[str] = []
        seen: set[tuple[str, str]] = set()
        for path in _python_files(_SOURCE_ROOTS):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom):
                    continue
                module_name = _absolute_module(path, node)
                if module_name is None or not module_name.startswith(_OWNED_PREFIXES):
                    continue
                for alias in node.names:
                    if alias.name == "*" or (module_name, alias.name) in seen:
                        continue
                    seen.add((module_name, alias.name))
                    try:
                        getattr(importlib.import_module(module_name), alias.name)
                    except Exception as exc:
                        failures.append(
                            f"{module_name}.{alias.name}: {type(exc).__name__}: {exc}"
                        )
        self.assertEqual(failures, [])

    def test_controller_bundle_archives_source_paths(self) -> None:
        self.assertEqual(self.release["schema"], "automa_controller_bundle_manifest_v0")
        self.assertEqual(
            [source["package_root"] for source in self.release["sources"]],
            ["autonomy", "implementations"],
        )
        archived = [entry["archive_path"] for entry in self.release["files"]]
        recorded = [entry["workspace_relative_path"] for entry in self.release["files"]]
        self.assertEqual(archived, recorded)
        self.assertEqual(sorted(archived), _bundle_source_paths())
        for relative in archived:
            self.assertTrue((self.bundle_root / relative).is_file(), relative)
        manifest = json.loads(
            (self.bundle_root / "bundle-manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["tree_sha256"], self.release["tree_sha256"])

    def test_staged_perception_shares_one_bundle_copy(self) -> None:
        host_mapper = importlib.import_module("autonomy.perception.plugin_runner").PluginPerceptionMapper
        host_contract = importlib.import_module("autonomy.perception.plugin").PerceptionPluginContract
        mapper = _load_mapper(
            PERCEPTION_MAPPER_SPEC,
            {
                "plugins": ["frame"],
                "plugin_specs": {"frame": PERCEPTION_PLUGIN_SPECS["frame"]},
            },
            bundle_root=self.bundle_root,
        )
        self.assertIsNot(type(mapper), host_mapper)
        self.assertIsNot(type(mapper.plugins[0].contract), host_contract)

        # Both trees are evicted, so the plugin and the mapper bind the same
        # bundle classes rather than the classes already imported by the host.
        with StagedBundleImport(self.bundle_root, PERCEPTION_BUNDLE_PREFIXES).activate():
            mapper_module = importlib.import_module("autonomy.perception.plugin_runner")
            plugin_module = importlib.import_module("implementations.perception.observation.plugin")
            self.assertIn(str(self.bundle_root), mapper_module.__file__ or "")
            self.assertIn(str(self.bundle_root), plugin_module.__file__ or "")
            self.assertIs(plugin_module.PerceptionPluginContract, mapper_module.PerceptionPluginContract)
            self.assertIsNot(plugin_module.PerceptionPluginContract, host_contract)
            self.assertIsNot(mapper_module.PluginPerceptionMapper, host_mapper)

    def test_staged_memory_keeps_host_autonomy_classes(self) -> None:
        host_snapshot = importlib.import_module("autonomy.memory.values").MemorySnapshot
        host_context = importlib.import_module("autonomy.decision.cycle").DecisionFrameContext
        host_ledger = importlib.import_module(
            "implementations.memory.bounded_evidence"
        ).BoundedEvidenceLedger
        payload = build_memory_activation_payload()
        payload["controller_bundle"] = {"root_dir": str(self.bundle_root)}
        activation_path = self.bundle_root / "runtime" / "memory" / "active.json"
        activation_path.parent.mkdir(parents=True, exist_ok=True)
        activation_path.write_text(json.dumps(payload), encoding="utf-8")
        step = load_memory_step_from_bundle(read_memory_activation(activation_path))

        self.assertIsNot(type(step.implementation), host_ledger)
        self.assertIs(type(step.last_snapshot), host_snapshot)
        # autonomy stays imported from the host. Its package __path__ is the
        # host tree, including submodules imported for the first time here.
        with StagedBundleImport(self.bundle_root, MEMORY_BUNDLE_PREFIXES).activate():
            staged = importlib.import_module("implementations.memory.bounded_evidence")
            self.assertIn(str(self.bundle_root), staged.__file__ or "")
            self.assertIsNot(staged.BoundedEvidenceLedger, host_ledger)
            self.assertIs(staged.MemorySnapshot, host_snapshot)
            self.assertIs(staged.DecisionFrameContext, host_context)

    def test_memory_staging_ignores_autonomy_files_present_only_in_the_bundle(self) -> None:
        extra = self.bundle_root / "autonomy" / "vehicle" / "only_in_bundle.py"
        extra.write_text("MARKER = 'bundle'\n", encoding="utf-8")
        try:
            with StagedBundleImport(self.bundle_root, MEMORY_BUNDLE_PREFIXES).activate():
                with self.assertRaises(ModuleNotFoundError):
                    importlib.import_module("autonomy.vehicle.only_in_bundle")
            with StagedBundleImport(self.bundle_root, PERCEPTION_BUNDLE_PREFIXES).activate():
                loaded = importlib.import_module("autonomy.vehicle.only_in_bundle")
                self.assertEqual(loaded.MARKER, "bundle")
                self.assertIn(str(self.bundle_root), loaded.__file__ or "")
        finally:
            extra.unlink(missing_ok=True)
            sys.modules.pop("autonomy.vehicle.only_in_bundle", None)

    def test_memory_staging_loads_lab_modules_from_the_bundle(self) -> None:
        lab = self.bundle_root / "lab"
        lab.mkdir()
        (lab / "__init__.py").write_text("", encoding="utf-8")
        (lab / "probe.py").write_text("MARKER = 'bundle'\n", encoding="utf-8")
        with StagedBundleImport(self.bundle_root, MEMORY_BUNDLE_PREFIXES).activate():
            probe = importlib.import_module("lab.probe")
            self.assertEqual(probe.MARKER, "bundle")
            self.assertIn(str(self.bundle_root), probe.__file__ or "")
        self.assertNotIn("lab.probe", sys.modules)
