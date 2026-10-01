"""Contracts for moving modules without splitting loaded classes.

``loading_inventory`` is the baseline. Each legacy spec, export, and module
path must still resolve to the recorded owner. The structural suite
``tests/autonomy/test_loading_surface.py`` reports paths it does not record.

Staged bundle checks still fail when an archive stores a source file somewhere
other than its current path, or when a staged import uses a different class
than the host or its sibling bundle copy.
"""

from __future__ import annotations

import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

from cli.automa_cli.bundles import controller_bundle_paths, sync_controller_bundle
from cli.automa_cli.staged_bundle import StagedBundleImport
from cli.automa_cli.step_hosting import BUNDLE_PREFIXES, load_staged_runner
from implementations.decision_cycle.catalog import packaged_activation
from tests.integration.controller_bundle.loading_inventory import (
    LEGACY_EXPORTS,
    LEGACY_MODULES,
    LEGACY_SPECS,
)


ROOT = Path(__file__).resolve().parents[3]


def _load_spec(spec: str) -> object:
    module_name, separator, attribute = spec.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError(f"import spec must be 'module.path:name', got {spec!r}")
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

    def test_staged_steps_keep_host_autonomy_classes(self) -> None:
        host_contract = importlib.import_module(
            "autonomy.decision_cycle.perception.plugin"
        ).PerceptionPluginContract
        host_evidence = importlib.import_module("autonomy.decision_cycle.memory.evidence").RetainedEvidence
        host_ledger = importlib.import_module(
            "implementations.decision_cycle.memory.bounded_evidence.plugin"
        ).BoundedEvidenceLedger
        metadata = {"controller_bundle": {"root_dir": str(self.bundle_root)}}
        perception = load_staged_runner(packaged_activation("perception", ["frame"], metadata=metadata))
        memory = load_staged_runner(packaged_activation("memory", metadata=metadata))

        # Plugins come from the bundle copy; autonomy, and so every value class
        # the cycle passes between steps, stays the host's.
        self.assertIs(type(perception.runner.plugins[0].contract), host_contract)
        self.assertIsNot(type(memory.runner.plugins[0]), host_ledger)
        with StagedBundleImport(self.bundle_root, BUNDLE_PREFIXES).activate():
            staged = importlib.import_module(
                "implementations.decision_cycle.memory.bounded_evidence.plugin"
            )
            self.assertIn(str(self.bundle_root), staged.__file__ or "")
            self.assertIs(staged.RetainedEvidence, host_evidence)

    def test_staging_ignores_autonomy_files_present_only_in_the_bundle(self) -> None:
        extra = self.bundle_root / "autonomy" / "vehicle" / "only_in_bundle.py"
        extra.write_text("MARKER = 'bundle'\n", encoding="utf-8")
        try:
            with StagedBundleImport(self.bundle_root, BUNDLE_PREFIXES).activate():
                with self.assertRaises(ModuleNotFoundError):
                    importlib.import_module("autonomy.vehicle.only_in_bundle")
        finally:
            extra.unlink(missing_ok=True)
            sys.modules.pop("autonomy.vehicle.only_in_bundle", None)

    def test_staging_loads_lab_modules_from_the_bundle(self) -> None:
        lab = self.bundle_root / "lab"
        lab.mkdir()
        (lab / "__init__.py").write_text("", encoding="utf-8")
        (lab / "probe.py").write_text("MARKER = 'bundle'\n", encoding="utf-8")
        with StagedBundleImport(self.bundle_root, BUNDLE_PREFIXES).activate():
            probe = importlib.import_module("lab.probe")
            self.assertEqual(probe.MARKER, "bundle")
            self.assertIn(str(self.bundle_root), probe.__file__ or "")
        self.assertNotIn("lab.probe", sys.modules)
