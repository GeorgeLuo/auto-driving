"""Contracts for moving modules without splitting loaded classes.

``loading_inventory`` is the baseline. Each legacy spec, export, and module
path must still resolve to the recorded owner. Scanning the tree does not
rebuild that baseline: a catalog or export list can stop mentioning an old
path without dropping the check. The scan only reports a path that is not
already recorded, which means the change reaches consumers outside the
inventory.

Staged bundle checks still fail when an archive stores a source file somewhere
other than its current path, or when a staged import uses a different class
than the host or its sibling bundle copy.
"""

from __future__ import annotations

import ast
import importlib
import json
import subprocess
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
from tests.integration.controller_bundle.loading_inventory import (
    LEGACY_EXPORTS,
    LEGACY_MODULES,
    LEGACY_SPECS,
)


ROOT = Path(__file__).resolve().parents[3]
_SOURCE_ROOTS = (
    ROOT / "autonomy",
    ROOT / "implementations",
    ROOT / "cli",
    ROOT / "scripts",
    ROOT / "deploy" / "targets" / "donkeycar" / "app",
)
_SPEC_PREFIXES = ("autonomy.", "implementations.", "cli.", "lab.")
_MODULE_PREFIXES = ("autonomy.", "implementations.", "lab.", "cli.")
_IMPORT_PREFIXES = ("autonomy", "implementations", "cli")
_MANIFEST_SPEC_KEYS = ("entrypoint", "provider_spec", "implementation_spec")
_OUTSIDE_BASELINE = (
    "paths outside the migration baseline; this change reaches consumers "
    "the inventory does not pin"
)


def _python_files(roots: tuple[Path, ...]) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts or "vendor" in path.parts:
                continue
            files.append(path)
    return files


def _git_files(pattern: str) -> list[Path]:
    # Tracked lab sources only. Untracked experiments are not consumers.
    output = subprocess.check_output(["git", "ls-files", pattern], cwd=ROOT, text=True)
    return [ROOT / line for line in output.splitlines() if line]


def _module_name(path: Path) -> str:
    parts = path.relative_to(ROOT).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


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


def _is_dotted_path(value: str) -> bool:
    if ":" in value or not value.startswith(_MODULE_PREFIXES):
        return False
    return all(part.isidentifier() for part in value.split("."))


def _load_spec(spec: str) -> object:
    module_name, separator, attribute = spec.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError(f"import spec must be 'module.path:name', got {spec!r}")
    return getattr(importlib.import_module(module_name), attribute)


def _string_constants(path: Path) -> list[tuple[str, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            docstrings.add(id(first.value))
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if id(node) in docstrings:
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.append((node.value, node.lineno))
    return found


def _static_exports(path: Path) -> list[str] | None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = False
    names: list[str] | None = []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets):
            continue
        found = True
        value = node.value
        if isinstance(value, (ast.List, ast.Tuple)) and all(
            isinstance(element, ast.Constant) and isinstance(element.value, str)
            for element in value.elts
        ):
            names = [element.value for element in value.elts]
        else:
            names = None
    if not found:
        return []
    return names


def _manifest_specs(value: object) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in _MANIFEST_SPEC_KEYS and isinstance(item, str):
                found.append((key, item))
            else:
                found.extend(_manifest_specs(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_manifest_specs(item))
    return found


def _known_specs() -> set[str]:
    known: set[str] = set()
    for spec, module_name, attribute in LEGACY_SPECS:
        known.add(spec)
        known.add(f"{module_name}:{attribute}")
    return known


def _known_exports() -> set[tuple[str, str]]:
    known: set[tuple[str, str]] = set()
    for legacy_module, legacy_name, canonical_module, canonical_name in LEGACY_EXPORTS:
        known.add((legacy_module, legacy_name))
        known.add((canonical_module, canonical_name))
    return known


def _known_modules() -> set[str]:
    known: set[str] = set()
    for legacy_module, canonical_module in LEGACY_MODULES:
        known.add(legacy_module)
        known.add(canonical_module)
    return known


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

    def test_legacy_paths_resolve_to_canonical_owners(self) -> None:
        specs = [spec for spec, _, _ in LEGACY_SPECS]
        exports = [(module_name, name) for module_name, name, _, _ in LEGACY_EXPORTS]
        modules = [module_name for module_name, _ in LEGACY_MODULES]
        self.assertEqual(len(specs), len(set(specs)))
        self.assertEqual(len(exports), len(set(exports)))
        self.assertEqual(len(modules), len(set(modules)))

        mismatches: list[str] = []
        for spec, module_name, attribute in LEGACY_SPECS:
            try:
                loaded = _load_spec(spec)
                owner = getattr(importlib.import_module(module_name), attribute)
            except Exception as exc:
                mismatches.append(f"{spec}: {type(exc).__name__}: {exc}")
                continue
            if loaded is not owner:
                mismatches.append(f"{spec} is not {module_name}.{attribute}")
        for legacy_module, legacy_name, canonical_module, canonical_name in LEGACY_EXPORTS:
            try:
                loaded = getattr(importlib.import_module(legacy_module), legacy_name)
                owner = getattr(importlib.import_module(canonical_module), canonical_name)
            except Exception as exc:
                mismatches.append(
                    f"{legacy_module}.{legacy_name}: {type(exc).__name__}: {exc}"
                )
                continue
            if loaded is not owner:
                mismatches.append(
                    f"{legacy_module}.{legacy_name} is not {canonical_module}.{canonical_name}"
                )
        for legacy_module, canonical_module in LEGACY_MODULES:
            for module_name in (legacy_module, canonical_module):
                try:
                    importlib.import_module(module_name)
                except Exception as exc:
                    mismatches.append(f"{module_name}: {type(exc).__name__}: {exc}")
        self.assertEqual(mismatches, [], "legacy path does not resolve to its canonical owner")

    def test_engine_spec_modules_reload_their_owner(self) -> None:
        # Runs in a fresh process because reloading replaces the engine classes.
        script = """
import importlib
import sys
from autonomy.runtime.manager import AutonomyManager

for legacy, owner, class_name in (
    ("implementations.decision.shadow_adapter", "implementations.runtime.engines.hold_action", "ShadowProposalsAutonomyEngine"),
    ("implementations.decision.live_adapter", "implementations.runtime.engines.mode_gated_action", "ObstacleAvoidanceAutonomyEngine"),
):
    assert importlib.import_module(legacy) is importlib.import_module(owner)
    manager = AutonomyManager(default_engine_spec=f"{legacy}:{class_name}")
    before = type(manager.engine)
    manager.reload_engine()
    after = type(manager.engine)
    assert after is not before, legacy
    assert after is getattr(sys.modules[owner], class_name), legacy
    assert sys.modules[legacy] is sys.modules[owner], legacy
"""
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_changes_outside_the_baseline_are_visible(self) -> None:
        # Absence of a recorded path from the tree is not a failure. A new spec,
        # export, lab import, or manifest field that the inventory does not
        # already name is a larger consumer surface than the baseline.
        known_specs = _known_specs()
        known_exports = _known_exports()
        known_modules = _known_modules()
        outside: list[str] = []

        for path in _python_files(_SOURCE_ROOTS) + _git_files("lab/**/*.py"):
            relative = path.relative_to(ROOT).as_posix()
            for value, lineno in _string_constants(path):
                location = f"{relative}:{lineno}"
                if _is_import_spec(value):
                    if value not in known_specs:
                        outside.append(f"{location} spec {value}")
                    continue
                if not _is_dotted_path(value):
                    continue
                try:
                    importlib.import_module(value)
                except ModuleNotFoundError:
                    module_name, separator, attribute = value.rpartition(".")
                    if not separator:
                        continue
                    try:
                        getattr(importlib.import_module(module_name), attribute)
                    except Exception:
                        continue
                    if (module_name, attribute) not in known_exports:
                        outside.append(f"{location} import {module_name}.{attribute}")
                else:
                    if value not in known_modules:
                        outside.append(f"{location} module {value}")

        for path in _git_files("lab/**/plugin.json"):
            relative = path.relative_to(ROOT).as_posix()
            payload = json.loads(path.read_text(encoding="utf-8"))
            for key, value in _manifest_specs(payload):
                if not _is_import_spec(value) or value not in known_specs:
                    outside.append(f"{relative} {key} {value}")

        for path in _python_files((ROOT / "autonomy", ROOT / "implementations")):
            relative = path.relative_to(ROOT).as_posix()
            exported = _static_exports(path)
            if exported is None:
                outside.append(f"{relative} __all__ is not a static name list")
                continue
            module_name = _module_name(path)
            for name in exported:
                if (module_name, name) not in known_exports:
                    outside.append(f"{relative} export {module_name}.{name}")

        for path in _git_files("lab/**/*.py"):
            relative = path.relative_to(ROOT).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    if node.level:
                        continue
                    module_name = node.module or ""
                    if module_name not in _IMPORT_PREFIXES and not module_name.startswith(
                        tuple(prefix + "." for prefix in _IMPORT_PREFIXES)
                    ):
                        continue
                    for alias in node.names:
                        if alias.name == "*":
                            outside.append(f"{relative}:{node.lineno} import {module_name}.*")
                            continue
                        if (module_name, alias.name) not in known_exports:
                            outside.append(
                                f"{relative}:{node.lineno} import {module_name}.{alias.name}"
                            )
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name in _IMPORT_PREFIXES or alias.name.startswith(
                            tuple(prefix + "." for prefix in _IMPORT_PREFIXES)
                        ):
                            if alias.name not in known_modules:
                                outside.append(f"{relative}:{node.lineno} module {alias.name}")

        self.assertEqual(outside, [], _OUTSIDE_BASELINE)

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
            mapper_module = importlib.import_module(
                "autonomy.decision_cycle.perception.plugin_runner"
            )
            plugin_module = importlib.import_module(
                "implementations.decision_cycle.perception.frame_observation.plugin"
            )
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
            staged = importlib.import_module(
                "implementations.decision_cycle.memory.bounded_evidence.plugin"
            )
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
