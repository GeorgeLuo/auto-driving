"""Contracts for moving modules without splitting loaded classes.

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

from autonomy.runtime.plugin_loader import BUNDLE_PREFIXES, StagedBundleImport, load_runner
from cli.automa_cli.hosts.bundles import sync_controller_bundle
from cli.automa_cli.releases.paths import controller_bundle_paths
from implementations.decision_cycle.catalog import packaged_activation


ROOT = Path(__file__).resolve().parents[4]


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
        cls.release = sync_controller_bundle(cls.bundle, emit=None)
        cls.bundle_root = Path(cls.bundle["root_dir"])

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temporary.cleanup()

    def test_controller_bundle_archives_source_paths(self) -> None:
        self.assertEqual(self.release["schema"], "automa_controller_bundle_manifest")
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
            "implementations.decision_cycle.memory.plugins.bounded_evidence.plugin"
        ).BoundedEvidenceLedger
        metadata = {"controller_bundle": {"root_dir": str(self.bundle_root)}}
        perception = load_runner(packaged_activation("perception", ["frame"], metadata=metadata))
        memory = load_runner(packaged_activation("memory", metadata=metadata))

        # Plugins come from the bundle copy; autonomy, and so every value class
        # the cycle passes between steps, stays the host's.
        self.assertIs(type(perception.runner.plugins["frame"].contract), host_contract)
        self.assertIsNot(
            type(memory.runner.plugins["bounded_evidence"].implementation), host_ledger
        )
        with StagedBundleImport(self.bundle_root, BUNDLE_PREFIXES).activate():
            staged = importlib.import_module(
                "implementations.decision_cycle.memory.shared.evidence_ledger.reduction"
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
