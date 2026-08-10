"""Deterministic tests for M007-10 continuity contract helpers."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import importlib.util

_CC_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs/milestones/007-cli-operator-usability/tools/live-cli-session-runner/continuity_contract.py"
)
_spec = importlib.util.spec_from_file_location("continuity_contract_under_test", _CC_PATH)
assert _spec and _spec.loader
cc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cc)

def _full_product_map(fill: str = "ddd") -> dict[str, str]:
    """Minimal complete product key set for finalizer unit tests."""
    product = {rel: f"{fill}-{i}" for i, rel in enumerate(sorted(cc.DEFAULT_PRODUCT_RELATIVE_PATHS))}
    for tree in cc.DEFAULT_PRODUCT_TREE_ROOTS:
        product[f"{tree}/"] = f"{fill}-tree-{tree}"
    return product


def _identity_fixture(
    root: Path, *, autonomy_symlink_target: str | None = None
) -> Path:
    """Create a complete disposable identity bundle and return its catalog."""

    for rel in cc.DEFAULT_PRODUCT_RELATIVE_PATHS:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {rel}\n", encoding="utf-8")
    for tree in cc.DEFAULT_PRODUCT_TREE_ROOTS:
        if tree == "autonomy" and autonomy_symlink_target is not None:
            continue
        path = root / tree / "__init__.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {tree}\n", encoding="utf-8")

    if autonomy_symlink_target is not None:
        target = root / autonomy_symlink_target
        (target / "runtime").mkdir(parents=True)
        (target / "runtime" / "engine.py").write_text("engine\n", encoding="utf-8")
        (target / "runtime" / "manager.py").write_text("manager\n", encoding="utf-8")
        (root / "autonomy").symlink_to(autonomy_symlink_target, target_is_directory=True)

    tool_dir = root / (
        "docs/milestones/007-cli-operator-usability/tools/live-cli-session-runner"
    )
    catalog_path = tool_dir / "catalogs/m007-continuity.yaml"
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    catalog_path.write_text("id: m007-continuity\n", encoding="utf-8")
    (tool_dir / "session_runner.py").write_text("# runner\n", encoding="utf-8")
    (tool_dir / "continuity_contract.py").write_text("# contract\n", encoding="utf-8")
    return catalog_path



def _base_catalog(steps: list[dict]) -> dict:
    return {
        "schema": "live_cli_session_catalog_v0",
        "id": "m007-continuity-test",
        "track": "continuity",
        "steps": steps,
    }


def _full_topology_catalog(*, visual: bool = True, offline_record: bool = True) -> dict:
    """Minimal catalog that satisfies full family topology when well-formed."""

    offline_run = [
        "./cli/automa",
        "vehicles",
        "perception",
        "run",
        "--id",
        "x",
        "--frames",
        "2",
    ]
    if offline_record:
        offline_run.append("--record")
    return _base_catalog(
        [
            {
                "id": "offline-capture",
                "family_id": "continuity.offline_perception",
                "safety": "local_write",
                "required_for_verdict": True,
                "primary_cue": "human summary leads with review path and frame counts",
                "commands": [offline_run],
            },
            {
                "id": "offline-apply",
                "family_id": "continuity.offline_perception",
                "safety": "local_write",
                "required_for_verdict": True,
                "primary_cue": "review path first; exclusive apply run directory",
                "commands": [
                    [
                        "./cli/automa",
                        "vehicles",
                        "perception",
                        "apply",
                        "{src_dir}",
                        "--algorithm",
                        "lightweight_observer",
                        "--record",
                    ]
                ],
            },
            {
                "id": "live-swap-stage",
                "family_id": "continuity.live_config_swap",
                "safety": "live_mutation",
                "required_for_verdict": True,
                "visual_required": visual,
                "visual_prompt": "Automa view nonblank after restage?",
                "primary_cue": "Ready for: inspect perception; worker running; observe_only",
                "commands": [
                    [
                        "./cli/automa",
                        "vehicles",
                        "update",
                        "perception",
                        "--id",
                        "x",
                        "--algorithm",
                        "lightweight_observer",
                    ],
                    [
                        "./cli/automa",
                        "vehicles",
                        "automation",
                        "run",
                        "--id",
                        "x",
                        "--observe-only",
                        "--frames",
                        "0",
                        "--open-view",
                    ],
                ],
            },
            {
                "id": "live-swap-stop",
                "family_id": "continuity.live_config_swap",
                "safety": "live_mutation",
                "required_for_verdict": True,
                "visual_required": False,
                "primary_cue": "worker stopped; activation restored",
                "commands": [
                    ["./cli/automa", "vehicles", "automation", "stop", "--id", "x"],
                ],
            },
            {
                "id": "memory-lifecycle",
                "family_id": "continuity.memory_lifecycle",
                "safety": "live_mutation",
                "required_for_verdict": True,
                "primary_cue": "Memory check: ... PASS (present/dropout/expiry/reset)",
                "commands": [
                    [
                        "./cli/automa",
                        "vehicles",
                        "memory",
                        "check",
                        "--id",
                        "x",
                        "--record",
                    ]
                ],
            },
        ]
    )


class ContinuitySafetyTests(unittest.TestCase):
    def test_rejects_movement_leaf(self) -> None:
        catalog = _base_catalog(
            [
                {
                    "id": "bad",
                    "family_id": "continuity.offline_perception",
                    "safety": "read",
                    "commands": [
                        ["./cli/automa", "vehicles", "operation", "startup-check", "--id", "x"]
                    ],
                },
            ]
        )
        ok, reason, _ = cc.validate_continuity_safety_preflight(catalog)
        self.assertFalse(ok)
        self.assertIn("forbidden", reason)

    def test_rejects_simulators_ensure(self) -> None:
        catalog = _base_catalog(
            [
                {
                    "id": "bad",
                    "family_id": "continuity.offline_perception",
                    "safety": "local_write",
                    "commands": [
                        ["./cli/automa", "simulators", "ensure", "--scenario", "x"]
                    ],
                },
            ]
        )
        ok, reason, _ = cc.validate_continuity_safety_preflight(catalog)
        self.assertFalse(ok, reason)

    def test_rejects_run_without_observe_only(self) -> None:
        ok, reason = cc.argv_allowed(
            ["./cli/automa", "vehicles", "automation", "run", "--id", "x"]
        )
        self.assertFalse(ok)
        self.assertTrue(
            "observe-only" in reason or "forbidden" in reason,
            reason,
        )

    def test_allowlists_observe_only_run(self) -> None:
        ok, reason = cc.argv_allowed(
            [
                "./cli/automa",
                "vehicles",
                "automation",
                "run",
                "--id",
                "x",
                "--observe-only",
                "--frames",
                "0",
            ]
        )
        self.assertTrue(ok, reason)

    def test_rejects_unregistered_flags(self) -> None:
        ok, reason = cc.argv_allowed(
            [
                "./cli/automa",
                "vehicles",
                "automation",
                "run",
                "--id",
                "x",
                "--observe-only",
                "--definitely-not-real",
            ]
        )
        self.assertFalse(ok, reason)
        self.assertTrue("unregistered" in reason or "allowlist" in reason, reason)

    def test_rejects_automation_record_even_if_parser_valid(self) -> None:
        ok, reason = cc.argv_allowed(
            [
                "./cli/automa",
                "vehicles",
                "automation",
                "run",
                "--id",
                "x",
                "--observe-only",
                "--record",
            ]
        )
        self.assertFalse(ok, reason)
        self.assertTrue(
            "record" in reason.lower() or "forbidden" in reason.lower() or "allowlist" in reason,
            reason,
        )

    def test_rejects_verbose_ok_but_log_forbidden_on_automation(self) -> None:
        ok, reason = cc.argv_allowed(
            [
                "./cli/automa",
                "vehicles",
                "automation",
                "run",
                "--id",
                "x",
                "--observe-only",
                "--log",
            ]
        )
        self.assertFalse(ok, reason)


class ContinuityFamilyTests(unittest.TestCase):
    def test_missing_required_family(self) -> None:
        catalog = _base_catalog(
            [
                {
                    "id": "a",
                    "family_id": "continuity.offline_perception",
                    "required_for_verdict": True,
                    "primary_cue": "review path first",
                    "commands": [
                        [
                            "./cli/automa",
                            "vehicles",
                            "perception",
                            "run",
                            "--id",
                            "x",
                            "--record",
                        ]
                    ],
                }
            ]
        )
        ok, reason, _ = cc.validate_continuity_families(catalog)
        self.assertFalse(ok)
        self.assertIn("missing required", reason)

    def test_help_only_required_family_rejected(self) -> None:
        catalog = _full_topology_catalog()
        catalog["steps"][0]["commands"] = [["./cli/automa", "help"]]
        ok, reason, _ = cc.validate_continuity_families(catalog)
        self.assertFalse(ok)
        self.assertIn("help/status-only", reason)

    def test_thin_offline_without_record_rejected(self) -> None:
        catalog = _full_topology_catalog(offline_record=False)
        ok, reason, _ = cc.validate_continuity_families(catalog)
        self.assertFalse(ok, reason)
        self.assertIn("recorded", reason)

    def test_thin_offline_hardcoded_tmp_rejected(self) -> None:
        catalog = _full_topology_catalog()
        catalog["steps"][1]["commands"] = [
            [
                "./cli/automa",
                "vehicles",
                "perception",
                "apply",
                "/tmp/some-run",
                "--algorithm",
                "lightweight_observer",
                "--record",
            ]
        ]
        ok, reason, _ = cc.validate_continuity_families(catalog)
        self.assertFalse(ok, reason)
        self.assertTrue("src_dir" in reason or "/tmp" in reason, reason)

    def test_live_without_visual_required_rejected(self) -> None:
        catalog = _full_topology_catalog(visual=False)
        ok, reason, _ = cc.validate_continuity_families(catalog)
        self.assertFalse(ok, reason)
        self.assertIn("visual_required", reason)

    def test_duplicate_step_ids_rejected(self) -> None:
        catalog = _full_topology_catalog()
        catalog["steps"][1]["id"] = catalog["steps"][0]["id"]
        ok, reason, _ = cc.validate_continuity_families(catalog)
        self.assertFalse(ok, reason)
        self.assertIn("duplicate", reason)

    def test_path_only_primary_cue_rejected(self) -> None:
        catalog = _full_topology_catalog()
        catalog["steps"][0]["primary_cue"] = "/tmp/only/a/path.json"
        ok, reason, _ = cc.validate_continuity_families(catalog)
        self.assertFalse(ok, reason)
        self.assertIn("primary_cue", reason)

    def test_well_formed_topology_accepted(self) -> None:
        catalog = _full_topology_catalog()
        ok, reason, _ = cc.validate_continuity_families(catalog)
        self.assertTrue(ok, reason)
        ok_s, reason_s, _ = cc.validate_continuity_safety_preflight(catalog)
        self.assertTrue(ok_s, reason_s)

    def test_partial_does_not_pass_family(self) -> None:
        aggregates = cc.aggregate_family_status(
            [
                {"family_id": "continuity.offline_perception", "status": "partial"},
                {"family_id": "continuity.live_config_swap", "status": "passed"},
                {"family_id": "continuity.memory_lifecycle", "status": "passed"},
            ]
        )
        self.assertEqual(aggregates["continuity.offline_perception"], "partial")
        ok, reason = cc.overall_pass_allowed(
            family_aggregates=aggregates,
            safety_preflight_ok=True,
            finalizer_ok=True,
        )
        self.assertFalse(ok)
        self.assertIn("offline_perception", reason)

    def test_visual_skip_keeps_family_partial_even_if_sibling_pass(self) -> None:
        aggregates = cc.aggregate_family_status(
            [
                {
                    "family_id": "continuity.live_config_swap",
                    "status": "skip",
                    "visual_required": True,
                    "machine_ok": True,
                },
                {
                    "family_id": "continuity.live_config_swap",
                    "status": "pass",
                    "visual_required": False,
                    "machine_ok": True,
                },
            ]
        )
        self.assertEqual(aggregates["continuity.live_config_swap"], "partial")

    def test_required_nonvisual_skip_keeps_family_partial(self) -> None:
        aggregates = cc.aggregate_family_status(
            [
                {
                    "family_id": "continuity.live_config_swap",
                    "status": "passed",
                    "required_for_verdict": True,
                },
                {
                    "family_id": "continuity.live_config_swap",
                    "status": "skip",
                    "required_for_verdict": True,
                    "visual_required": False,
                },
            ]
        )
        self.assertEqual(aggregates["continuity.live_config_swap"], "partial")

    def test_passed_when_all_required_passed(self) -> None:
        aggregates = {
            "continuity.offline_perception": "passed",
            "continuity.live_config_swap": "passed",
            "continuity.memory_lifecycle": "passed",
        }
        ok, _ = cc.overall_pass_allowed(
            family_aggregates=aggregates,
            safety_preflight_ok=True,
            finalizer_ok=True,
        )
        self.assertTrue(ok)


class ContinuityRestoreAndFinalizerTests(unittest.TestCase):
    def test_hash_only_snapshot_not_restorable(self) -> None:
        snap = {
            "ok": True,
            "restorable_bytes": None,
            "sha256": "abc",
            "path": "/tmp/x",
            "existed": True,
        }
        self.assertFalse(cc.snapshot_is_restorable(snap))
        result = cc.restore_activation(snap)
        self.assertFalse(result["ok"])

    def test_restorable_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "active.json"
            path.write_text('{"schema":"test","v":1}\n', encoding="utf-8")
            snap = cc.snapshot_activation(path)
            self.assertTrue(cc.snapshot_is_restorable(snap))
            path.write_text('{"schema":"test","v":2}\n', encoding="utf-8")
            restored = cc.restore_activation(snap)
            self.assertTrue(restored["ok"], restored)
            self.assertEqual(path.read_text(encoding="utf-8"), '{"schema":"test","v":1}\n')

    def test_absent_optional_removes_trial_created_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vehicle = "v1"
            base = root / "runtime" / "vehicles" / vehicle / "bundle" / "runtime"
            perc = base / "perception" / "active.json"
            perc.parent.mkdir(parents=True)
            perc.write_text('{"k":"perception"}\n', encoding="utf-8")
            mem = base / "memory" / "active.json"
            # snapshot while memory absent
            snap = cc.snapshot_staged_state(root, vehicle)
            self.assertTrue(cc.snapshot_is_restorable(snap), snap)
            self.assertFalse((snap["files"]["memory"]).get("existed"))
            # trial creates memory activation
            mem.parent.mkdir(parents=True, exist_ok=True)
            mem.write_text('{"k":"trial"}\n', encoding="utf-8")
            restored = cc.restore_activation(snap)
            self.assertTrue(restored["ok"], restored)
            self.assertFalse(mem.is_file(), "trial memory activation must be removed")

    def test_staged_snapshot_restores_bundle_trees(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vehicle = "v1"
            bundle = root / "runtime" / "vehicles" / vehicle / "bundle"
            runtime = bundle / "runtime"
            for name in ("perception", "decision", "memory"):
                path = runtime / name / "active.json"
                path.parent.mkdir(parents=True)
                path.write_text(f'{{"k":"{name}"}}\n', encoding="utf-8")
            auto = bundle / "autonomy" / "pkg.py"
            auto.parent.mkdir(parents=True)
            auto.write_text("prior = 1\n", encoding="utf-8")
            impl = bundle / "implementations" / "x.py"
            impl.parent.mkdir(parents=True)
            impl.write_text("prior_impl = 1\n", encoding="utf-8")
            (bundle / "bundle-manifest.json").write_text('{"tree":"prior"}\n', encoding="utf-8")
            cache = root / "cache"
            snap = cc.snapshot_staged_state(root, vehicle, cache_dir=cache)
            self.assertTrue(cc.snapshot_is_restorable(snap), snap)
            # trial mutates trees and activations
            auto.write_text("prior = 2\n", encoding="utf-8")
            impl.write_text("prior_impl = 2\n", encoding="utf-8")
            (runtime / "perception" / "active.json").write_text('{"k":"mut"}\n', encoding="utf-8")
            restored = cc.restore_activation(snap)
            self.assertTrue(restored["ok"], restored)
            self.assertEqual(auto.read_text(encoding="utf-8"), "prior = 1\n")
            self.assertEqual(impl.read_text(encoding="utf-8"), "prior_impl = 1\n")
            self.assertEqual(
                (runtime / "perception" / "active.json").read_text(encoding="utf-8"),
                '{"k":"perception"}\n',
            )

    def test_finalizer_refuses_stale_catalog_digest(self) -> None:
        recorded = {
            "catalog_sha256": "aaa",
            "runner_sha256": "bbb",
            "continuity_contract_sha256": "ccc",
            "product_sha256": _full_product_map("ddd"),
            "metrics_ui": None,
        }
        current = dict(recorded)
        current["catalog_sha256"] = "zzz"
        ok, reason = cc.finalize_evidence_freshness(recorded, current)
        self.assertFalse(ok)
        self.assertIn("catalog_sha256", reason)

    def test_finalizer_refuses_missing_digests(self) -> None:
        ok, reason = cc.finalize_evidence_freshness(
            {
                "catalog_sha256": None,
                "runner_sha256": "bbb",
                "continuity_contract_sha256": "ccc",
                "product_sha256": _full_product_map("ddd"),
            },
            {
                "catalog_sha256": "aaa",
                "runner_sha256": "bbb",
                "continuity_contract_sha256": "ccc",
                "product_sha256": _full_product_map("ddd"),
            },
        )
        self.assertFalse(ok)
        self.assertIn("missing", reason)

    def test_finalizer_refuses_empty_product_set(self) -> None:
        ok, reason = cc.finalize_evidence_freshness(
            {
                "catalog_sha256": "aaa",
                "runner_sha256": "bbb",
                "continuity_contract_sha256": "ccc",
                "product_sha256": {},
            },
            {
                "catalog_sha256": "aaa",
                "runner_sha256": "bbb",
                "continuity_contract_sha256": "ccc",
                "product_sha256": {},
            },
        )
        self.assertFalse(ok)
        self.assertIn("product_sha256", reason)

    def test_finalizer_ok_when_identical(self) -> None:
        bundle = {
            "catalog_sha256": "aaa",
            "runner_sha256": "bbb",
            "continuity_contract_sha256": "ccc",
            "product_sha256": _full_product_map("ddd"),
            "metrics_ui": {"commit": "abc", "worktree_state": "clean"},
            "metrics_ui_required": True,
        }
        ok, reason = cc.finalize_evidence_freshness(bundle, bundle)
        self.assertTrue(ok, reason)

    def test_stale_session_against_tree_fails(self) -> None:
        """session-A / tree-B: mutating a product file invalidates recorded identity."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # minimal product surface + catalog + runner copies
            for rel in cc.DEFAULT_PRODUCT_RELATIVE_PATHS:
                p = root / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(f"# {rel}\n", encoding="utf-8")
            for tree in cc.DEFAULT_PRODUCT_TREE_ROOTS:
                (root / tree / "__init__.py").parent.mkdir(parents=True, exist_ok=True)
                (root / tree / "__init__.py").write_text(f"# {tree}\n", encoding="utf-8")
            cat = (
                root
                / "docs/milestones/007-cli-operator-usability/tools/live-cli-session-runner/catalogs"
            )
            cat.mkdir(parents=True)
            catalog_path = cat / "m007-continuity.yaml"
            catalog_path.write_text("id: m007-continuity\n", encoding="utf-8")
            runner = (
                root
                / "docs/milestones/007-cli-operator-usability/tools/live-cli-session-runner/session_runner.py"
            )
            runner.parent.mkdir(parents=True, exist_ok=True)
            runner.write_text("# runner\n", encoding="utf-8")
            continuity = runner.parent / "continuity_contract.py"
            continuity.write_text("# contract\n", encoding="utf-8")

            recorded = cc.collect_identity_bundle(
                repo_root=root, catalog_path=catalog_path
            )
            recorded["metrics_ui_required"] = False
            session = root / "session"
            session.mkdir()
            (session / "result.json").write_text(
                json.dumps(
                    {
                        "result": "incomplete",
                        "continuity": {"identity_recorded": recorded},
                    }
                ),
                encoding="utf-8",
            )
            # mutate one product file (tree B)
            (root / "cli/automa_cli/automation.py").write_text("# mutated\n", encoding="utf-8")
            out = cc.validate_session_against_tree(
                session, repo_root=root, catalog_path=catalog_path
            )
            self.assertFalse(out["ok"], out)
            self.assertIn("mismatch", out["reason"].lower() + out.get("reason", ""))

    def test_render_launcher_and_runtime_changes_invalidate_identity(self) -> None:
        for relative in (
            "cli/automa",
            "cli/automa_cli/perception_view.html",
            "cli/automa_cli/physical_observation.py",
        ):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                for rel in cc.DEFAULT_PRODUCT_RELATIVE_PATHS:
                    path = root / rel
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(f"# {rel}\n", encoding="utf-8")
                for tree in cc.DEFAULT_PRODUCT_TREE_ROOTS:
                    path = root / tree / "__init__.py"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(f"# {tree}\n", encoding="utf-8")
                changed = root / relative
                changed.parent.mkdir(parents=True, exist_ok=True)
                changed.write_text("baseline\n", encoding="utf-8")
                tool_dir = root / (
                    "docs/milestones/007-cli-operator-usability/tools/live-cli-session-runner"
                )
                catalog_path = tool_dir / "catalogs/m007-continuity.yaml"
                catalog_path.parent.mkdir(parents=True, exist_ok=True)
                catalog_path.write_text("id: m007-continuity\n", encoding="utf-8")
                runner_path = tool_dir / "session_runner.py"
                runner_path.parent.mkdir(parents=True, exist_ok=True)
                runner_path.write_text("# runner\n", encoding="utf-8")
                (runner_path.parent / "continuity_contract.py").write_text(
                    "# contract\n", encoding="utf-8"
                )

                recorded = cc.collect_identity_bundle(
                    repo_root=root, catalog_path=catalog_path
                )
                recorded["metrics_ui_required"] = False
                changed.write_text("mutated\n", encoding="utf-8")
                current = cc.collect_identity_bundle(
                    repo_root=root, catalog_path=catalog_path
                )
                ok, reason = cc.finalize_evidence_freshness(recorded, current)
                self.assertFalse(ok, reason)
                self.assertIn("product mismatch", reason)

    def test_product_launcher_mode_change_invalidates_freshness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            launcher = Path(tmp) / "cli" / "automa"
            launcher.parent.mkdir(parents=True)
            launcher.write_text("#!/bin/sh\necho automa\n", encoding="utf-8")
            launcher.chmod(0o755)

            recorded_product = _full_product_map("p")
            recorded_product["cli/automa"] = cc.tree_file_digest(launcher)
            recorded = {
                "catalog_sha256": "a",
                "runner_sha256": "b",
                "continuity_contract_sha256": "c",
                "product_sha256": recorded_product,
                "metrics_ui_required": False,
            }

            launcher.chmod(0o644)
            current_product = dict(recorded_product)
            current_product["cli/automa"] = cc.tree_file_digest(launcher)
            current = dict(recorded)
            current["product_sha256"] = current_product

            ok, reason = cc.finalize_evidence_freshness(recorded, current)
            self.assertFalse(ok, reason)
            self.assertIn("product mismatch cli/automa", reason)

    def test_product_regular_file_symlink_substitution_invalidates_freshness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            launcher = root / "cli" / "automa"
            launcher.parent.mkdir(parents=True)
            launcher.write_bytes(b"same payload\n")
            target = root / "target"
            target.write_bytes(b"same payload\n")

            regular_digest = cc.tree_file_digest(launcher)
            launcher.unlink()
            launcher.symlink_to("../target")
            symlink_digest = cc.tree_file_digest(launcher)
            self.assertNotEqual(regular_digest, symlink_digest)

            recorded_product = _full_product_map("p")
            recorded_product["cli/automa"] = regular_digest
            current_product = dict(recorded_product)
            current_product["cli/automa"] = symlink_digest
            base = {
                "catalog_sha256": "a",
                "runner_sha256": "b",
                "continuity_contract_sha256": "c",
                "metrics_ui_required": False,
            }
            ok, reason = cc.finalize_evidence_freshness(
                {**base, "product_sha256": recorded_product},
                {**base, "product_sha256": current_product},
            )
            self.assertFalse(ok, reason)
            self.assertIn("product mismatch cli/automa", reason)

    def test_product_symlink_target_change_invalidates_freshness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            launcher = root / "cli" / "automa"
            launcher.parent.mkdir(parents=True)
            (root / "target-a").write_bytes(b"same payload\n")
            (root / "target-b").write_bytes(b"same payload\n")
            launcher.symlink_to("../target-a")
            digest_a = cc.tree_file_digest(launcher)
            launcher.unlink()
            launcher.symlink_to("../target-b")
            digest_b = cc.tree_file_digest(launcher)
            self.assertNotEqual(digest_a, digest_b)

            product = _full_product_map("p")
            product["cli/automa"] = digest_a
            current_product = dict(product)
            current_product["cli/automa"] = digest_b
            base = {
                "catalog_sha256": "a",
                "runner_sha256": "b",
                "continuity_contract_sha256": "c",
                "metrics_ui_required": False,
            }
            ok, reason = cc.finalize_evidence_freshness(
                {**base, "product_sha256": product},
                {**base, "product_sha256": current_product},
            )
            self.assertFalse(ok, reason)
            self.assertIn("product mismatch cli/automa", reason)

    def test_product_tree_root_symlink_retarget_invalidates_full_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            recorded_root = base / "recorded"
            current_root = base / "current"
            recorded_catalog = _identity_fixture(
                recorded_root, autonomy_symlink_target="autonomy-target-a"
            )
            current_catalog = _identity_fixture(
                current_root, autonomy_symlink_target="autonomy-target-b"
            )

            recorded = cc.collect_identity_bundle(
                repo_root=recorded_root, catalog_path=recorded_catalog
            )
            current = cc.collect_identity_bundle(
                repo_root=current_root, catalog_path=current_catalog
            )
            recorded["metrics_ui_required"] = False
            current["metrics_ui_required"] = False

            self.assertNotEqual(
                recorded["product_sha256"]["autonomy/"],
                current["product_sha256"]["autonomy/"],
            )
            ok, reason = cc.finalize_evidence_freshness(recorded, current)
            self.assertFalse(ok, reason)
            self.assertIn("product mismatch autonomy/", reason)

    def test_product_tree_path_delimiter_collision_invalidates_full_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            recorded_root = base / "recorded"
            current_root = base / "current"
            recorded_catalog = _identity_fixture(recorded_root)
            current_catalog = _identity_fixture(current_root)

            recorded_runtime = recorded_root / "autonomy" / "runtime"
            recorded_runtime.mkdir(parents=True)
            first = recorded_runtime / "engine.py"
            first.write_text("engine\n", encoding="utf-8")
            (recorded_runtime / "manager.py").write_text("manager\n", encoding="utf-8")

            current_runtime = current_root / "autonomy" / "runtime"
            first_digest = cc.tree_file_digest(first)
            self.assertIsInstance(first_digest, str)
            collision_path = (
                current_runtime
                / f"engine.py:{first_digest}\nruntime"
                / "manager.py"
            )
            collision_path.parent.mkdir(parents=True)
            collision_path.write_text("manager\n", encoding="utf-8")

            recorded = cc.collect_identity_bundle(
                repo_root=recorded_root, catalog_path=recorded_catalog
            )
            current = cc.collect_identity_bundle(
                repo_root=current_root, catalog_path=current_catalog
            )
            recorded["metrics_ui_required"] = False
            current["metrics_ui_required"] = False

            self.assertNotEqual(
                recorded["product_sha256"]["autonomy/"],
                current["product_sha256"]["autonomy/"],
            )
            ok, reason = cc.finalize_evidence_freshness(recorded, current)
            self.assertFalse(ok, reason)
            self.assertIn("product mismatch autonomy/", reason)

    def test_product_tree_ancestor_pycache_name_does_not_omit_material(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "__pycache__" / "repo"
            catalog = _identity_fixture(root)
            engine = root / "autonomy" / "runtime" / "engine.py"
            engine.parent.mkdir(parents=True)
            engine.write_text("engine-v1\n", encoding="utf-8")

            recorded = cc.collect_identity_bundle(
                repo_root=root, catalog_path=catalog
            )
            self.assertIn(
                "runtime/engine.py",
                cc._dir_file_digests(root / "autonomy"),
            )
            engine.write_text("engine-v2\n", encoding="utf-8")
            current = cc.collect_identity_bundle(
                repo_root=root, catalog_path=catalog
            )

            self.assertNotEqual(
                recorded["product_sha256"]["autonomy/"],
                current["product_sha256"]["autonomy/"],
            )
            ok, reason = cc.finalize_evidence_freshness(recorded, current)
            self.assertFalse(ok, reason)
            self.assertIn("product mismatch autonomy/", reason)

    def test_product_tree_relative_cache_exclusions_are_stable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog = _identity_fixture(root)
            autonomy = root / "autonomy"
            before = cc.tree_content_sha256(autonomy)

            (autonomy / "__pycache__" / "ignored.py").parent.mkdir()
            (autonomy / "__pycache__" / "ignored.py").write_text(
                "ignored\n", encoding="utf-8"
            )
            (autonomy / "generated.pyc").write_bytes(b"ignored-pyc")
            (autonomy / "generated.pyo").write_bytes(b"ignored-pyo")

            after = cc.tree_content_sha256(autonomy)
            self.assertEqual(before, after)
            self.assertEqual(
                cc.collect_identity_bundle(
                    repo_root=root, catalog_path=catalog
                )["product_collection_errors"],
                {},
            )

    def test_unreadable_product_tree_file_fails_full_finalizer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog = _identity_fixture(root)
            recorded = cc.collect_identity_bundle(
                repo_root=root, catalog_path=catalog
            )
            unreadable = root / "autonomy" / "runtime" / "unreadable-extra.py"
            unreadable.parent.mkdir(parents=True)
            unreadable.write_text("secret\n", encoding="utf-8")
            unreadable.chmod(0)
            try:
                real_digest = cc.tree_file_digest

                def fail_unreadable(path: Path) -> str | None:
                    if path == unreadable:
                        return None
                    return real_digest(path)

                with mock.patch.object(cc, "tree_file_digest", side_effect=fail_unreadable):
                    current = cc.collect_identity_bundle(
                        repo_root=root, catalog_path=catalog
                    )
            finally:
                unreadable.chmod(0o644)

            self.assertIn("autonomy/", current["product_collection_errors"])
            ok, reason = cc.finalize_evidence_freshness(recorded, current)
            self.assertFalse(ok, reason)
            self.assertIn("product collection failed", reason)

    def test_inaccessible_product_tree_subtree_fails_full_finalizer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog = _identity_fixture(root)
            recorded = cc.collect_identity_bundle(
                repo_root=root, catalog_path=catalog
            )
            blocked = root / "autonomy" / "runtime" / "inaccessible-subtree"
            blocked.mkdir(parents=True)
            (blocked / "material.py").write_text("material\n", encoding="utf-8")
            blocked.chmod(0)
            try:
                real_scandir = cc.os.scandir

                def fail_subtree(path: Path):
                    if Path(path) == blocked:
                        raise PermissionError("test inaccessible subtree")
                    return real_scandir(path)

                with mock.patch.object(cc.os, "scandir", side_effect=fail_subtree):
                    current = cc.collect_identity_bundle(
                        repo_root=root, catalog_path=catalog
                    )
            finally:
                blocked.chmod(0o755)

            self.assertIn("autonomy/", current["product_collection_errors"])
            ok, reason = cc.finalize_evidence_freshness(recorded, current)
            self.assertFalse(ok, reason)
            self.assertIn("product collection failed", reason)

    def test_session_against_tree_ok_when_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel in cc.DEFAULT_PRODUCT_RELATIVE_PATHS:
                p = root / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(f"# {rel}\n", encoding="utf-8")
            for tree in cc.DEFAULT_PRODUCT_TREE_ROOTS:
                (root / tree / "__init__.py").parent.mkdir(parents=True, exist_ok=True)
                (root / tree / "__init__.py").write_text(f"# {tree}\n", encoding="utf-8")
            cat = (
                root
                / "docs/milestones/007-cli-operator-usability/tools/live-cli-session-runner/catalogs"
            )
            cat.mkdir(parents=True)
            catalog_path = cat / "m007-continuity.yaml"
            catalog_path.write_text("id: m007-continuity\n", encoding="utf-8")
            runner = (
                root
                / "docs/milestones/007-cli-operator-usability/tools/live-cli-session-runner/session_runner.py"
            )
            runner.parent.mkdir(parents=True, exist_ok=True)
            runner.write_text("# runner\n", encoding="utf-8")
            (runner.parent / "continuity_contract.py").write_text("# c\n", encoding="utf-8")
            recorded = cc.collect_identity_bundle(
                repo_root=root, catalog_path=catalog_path
            )
            recorded["metrics_ui_required"] = False
            session = root / "session"
            session.mkdir()
            (session / "result.json").write_text(
                json.dumps(
                    {
                        "result": "incomplete",
                        "continuity": {"identity_recorded": recorded},
                    }
                ),
                encoding="utf-8",
            )
            out = cc.validate_session_against_tree(
                session, repo_root=root, catalog_path=catalog_path
            )
            self.assertTrue(out["ok"], out)


class ContinuityLineageTests(unittest.TestCase):
    def test_capture_and_verify_content_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "run"
            frames = src / "frames"
            frames.mkdir(parents=True)
            f0 = frames / "frame_000000.png"
            f1 = frames / "frame_000001.png"
            f0.write_bytes(b"\x89PNG-frame0")
            f1.write_bytes(b"\x89PNG-frame1")
            run = {
                "frames": [
                    {"frame_id": "frame_000000", "image_path": str(f0)},
                    {"frame_id": "frame_000001", "image_path": str(f1)},
                ]
            }
            (src / "run.json").write_text(json.dumps(run), encoding="utf-8")
            lineage = cc.capture_source_lineage(src)
            self.assertTrue(lineage["ok"], lineage)
            self.assertEqual(lineage["frame_count"], 2)
            ok, reason = cc.verify_source_lineage(src, lineage)
            self.assertTrue(ok, reason)
            # mutation of ordered input fails verify
            f0.write_bytes(b"\x89PNG-MUTATED")
            ok2, reason2 = cc.verify_source_lineage(src, lineage)
            self.assertFalse(ok2)
            self.assertIn("mismatch", reason2)

    def test_missing_run_json_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "run"
            src.mkdir()
            lineage = cc.capture_source_lineage(src)
            self.assertFalse(lineage["ok"])
            self.assertIn("run.json", str(lineage.get("error")))


class ContinuityVerdictTests(unittest.TestCase):
    def test_hitl_incomplete_not_pass(self) -> None:
        verdict, reason = cc.derive_continuity_verdict(
            safety_preflight_ok=True,
            family_aggregates={
                "continuity.offline_perception": "passed",
                "continuity.live_config_swap": "partial",
                "continuity.memory_lifecycle": "passed",
            },
            restore_ok=True,
            cleanup_ok=True,
            finalizer_ok=True,
            findings=[],
            hitl_complete=False,
        )
        self.assertEqual(verdict, "incomplete")
        self.assertIn("partial", reason or "")

    def test_named_operator_is_required_for_continuity_pass(self) -> None:
        verdict, reason = cc.derive_continuity_verdict(
            safety_preflight_ok=True,
            family_aggregates={
                "continuity.offline_perception": "passed",
                "continuity.live_config_swap": "passed",
                "continuity.memory_lifecycle": "passed",
            },
            restore_ok=True,
            cleanup_ok=True,
            finalizer_ok=True,
            findings=[],
            hitl_complete=True,
            operator="   ",
        )
        self.assertEqual(verdict, "incomplete")
        self.assertIn("operator", reason or "")


class RunIdCollisionTests(unittest.TestCase):
    def test_run_ids_unique_under_same_second(self) -> None:
        from cli.automa_cli.perception_runs import _run_id

        ids = {_run_id("apply", "same-source") for _ in range(20)}
        self.assertEqual(len(ids), 20)


class CompareErrorDetailTests(unittest.TestCase):
    def test_compare_failures_keep_error_detail_field(self) -> None:
        # Structural contract: compare failure dicts include error_detail key in source.
        src = (
            Path(__file__).resolve().parents[2]
            / "cli/automa_cli/perception_runs.py"
        )
        text = src.read_text(encoding="utf-8")
        self.assertIn('"error_detail": raw', text)
        self.assertIn("Human table stays one-line", text)



class Us04TreeRestoreVerificationTests(unittest.TestCase):
    def test_absent_tree_removes_trial_created_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vehicle = "v1"
            bundle = root / "runtime" / "vehicles" / vehicle / "bundle"
            runtime = bundle / "runtime"
            perc = runtime / "perception" / "active.json"
            perc.parent.mkdir(parents=True)
            perc.write_text('{"k":"p"}\n', encoding="utf-8")
            # no autonomy tree at snapshot
            cache = root / "cache"
            snap = cc.snapshot_staged_state(root, vehicle, cache_dir=cache)
            self.assertTrue(cc.snapshot_is_restorable(snap), snap)
            self.assertFalse(snap["staged_trees"]["autonomy"]["existed"])
            # trial creates autonomy
            trial = bundle / "autonomy" / "trial.py"
            trial.parent.mkdir(parents=True)
            trial.write_text("trial=1\n", encoding="utf-8")
            restored = cc.restore_activation(snap)
            self.assertTrue(restored["ok"], restored)
            self.assertFalse((bundle / "autonomy").exists(), "trial autonomy tree must be removed")

    def test_corrupted_cache_fails_restore(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vehicle = "v1"
            bundle = root / "runtime" / "vehicles" / vehicle / "bundle"
            runtime = bundle / "runtime"
            for name in ("perception",):
                path = runtime / name / "active.json"
                path.parent.mkdir(parents=True)
                path.write_text(f'{{"k":"{name}"}}\n', encoding="utf-8")
            auto = bundle / "autonomy" / "pkg.py"
            auto.parent.mkdir(parents=True)
            auto.write_text("prior=1\n", encoding="utf-8")
            cache = root / "cache"
            snap = cc.snapshot_staged_state(root, vehicle, cache_dir=cache)
            # corrupt cache before restore
            (cache / "autonomy" / "pkg.py").write_text("CORRUPT\n", encoding="utf-8")
            restored = cc.restore_activation(snap)
            self.assertFalse(restored["ok"], restored)
            self.assertIn("corrupted", str(restored.get("error") or "").lower())

    def test_restored_tree_hash_verified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vehicle = "v1"
            bundle = root / "runtime" / "vehicles" / vehicle / "bundle"
            runtime = bundle / "runtime"
            perc = runtime / "perception" / "active.json"
            perc.parent.mkdir(parents=True)
            perc.write_text('{"k":"p"}\n', encoding="utf-8")
            auto = bundle / "autonomy" / "pkg.py"
            auto.parent.mkdir(parents=True)
            auto.write_text("prior=1\n", encoding="utf-8")
            cache = root / "cache"
            snap = cc.snapshot_staged_state(root, vehicle, cache_dir=cache)
            auto.write_text("mutated=2\n", encoding="utf-8")
            restored = cc.restore_activation(snap)
            self.assertTrue(restored["ok"], restored)
            self.assertEqual(auto.read_text(encoding="utf-8"), "prior=1\n")
            tree_res = restored["results"]["tree:autonomy"]
            self.assertTrue(tree_res.get("verified"))
            self.assertEqual(
                tree_res.get("tree_sha256"),
                snap["staged_trees"]["autonomy"]["tree_sha256"],
            )


class FinalizerRequiredKeysAndMetricsTests(unittest.TestCase):
    def test_omitted_required_product_key_fails(self) -> None:
        product = _full_product_map("x")
        incomplete = dict(product)
        incomplete.pop(next(iter(incomplete)))
        complete = _full_product_map("x")
        ok, reason = cc.finalize_evidence_freshness(
            {
                "catalog_sha256": "a",
                "runner_sha256": "b",
                "continuity_contract_sha256": "c",
                "product_sha256": incomplete,
            },
            {
                "catalog_sha256": "a",
                "runner_sha256": "b",
                "continuity_contract_sha256": "c",
                "product_sha256": complete,
            },
        )
        self.assertFalse(ok)
        self.assertIn("required", reason.lower())

    def test_product_key_set_mismatch_fails(self) -> None:
        rec = _full_product_map("x")
        cur = _full_product_map("x")
        cur["extra/file.py"] = "zzz"
        ok, reason = cc.finalize_evidence_freshness(
            {
                "catalog_sha256": "a",
                "runner_sha256": "b",
                "continuity_contract_sha256": "c",
                "product_sha256": rec,
            },
            {
                "catalog_sha256": "a",
                "runner_sha256": "b",
                "continuity_contract_sha256": "c",
                "product_sha256": cur,
            },
        )
        self.assertFalse(ok)
        self.assertIn("key set", reason.lower())

    def test_runtime_tree_change_invalidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel in cc.DEFAULT_PRODUCT_RELATIVE_PATHS:
                p = root / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(f"# {rel}\n", encoding="utf-8")
            for tree in cc.DEFAULT_PRODUCT_TREE_ROOTS:
                (root / tree / "__init__.py").parent.mkdir(parents=True, exist_ok=True)
                (root / tree / "__init__.py").write_text(f"# {tree}\n", encoding="utf-8")
            cat = root / "cat.yaml"
            cat.write_text("id: x\n", encoding="utf-8")
            runner = (
                root
                / "docs/milestones/007-cli-operator-usability/tools/live-cli-session-runner/session_runner.py"
            )
            runner.parent.mkdir(parents=True, exist_ok=True)
            runner.write_text("# r\n", encoding="utf-8")
            (runner.parent / "continuity_contract.py").write_text("# c\n", encoding="utf-8")
            recorded = cc.collect_identity_bundle(repo_root=root, catalog_path=cat)
            recorded["metrics_ui_required"] = False
            # mutate autonomy tree B
            (root / "autonomy" / "__init__.py").write_text("# mutated autonomy\n", encoding="utf-8")
            current = cc.collect_identity_bundle(repo_root=root, catalog_path=cat)
            ok, reason = cc.finalize_evidence_freshness(recorded, current)
            self.assertFalse(ok)
            self.assertIn("product mismatch", reason)

    def test_metrics_ui_a_to_b_fails_when_required(self) -> None:
        product = _full_product_map("p")
        rec = {
            "catalog_sha256": "a",
            "runner_sha256": "b",
            "continuity_contract_sha256": "c",
            "product_sha256": product,
            "metrics_ui_required": True,
            "metrics_ui": {"commit": "A", "worktree_state": "clean"},
        }
        cur = {
            "catalog_sha256": "a",
            "runner_sha256": "b",
            "continuity_contract_sha256": "c",
            "product_sha256": product,
            "metrics_ui_required": True,
            "metrics_ui": {"commit": "B", "worktree_state": "clean"},
        }
        ok, reason = cc.finalize_evidence_freshness(rec, cur)
        self.assertFalse(ok)
        self.assertIn("commit", reason)

    def test_dirty_metrics_ui_without_named_diff_fails(self) -> None:
        product = _full_product_map("p")
        bundle = {
            "catalog_sha256": "a",
            "runner_sha256": "b",
            "continuity_contract_sha256": "c",
            "product_sha256": product,
            "metrics_ui_required": True,
            "metrics_ui": {"commit": "A", "worktree_state": "dirty"},
        }
        ok, reason = cc.finalize_evidence_freshness(bundle, bundle)
        self.assertFalse(ok)
        self.assertIn("named diff", reason.lower())

    def test_posthoc_does_not_reuse_recorded_metrics_ui(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel in cc.DEFAULT_PRODUCT_RELATIVE_PATHS:
                p = root / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(f"# {rel}\n", encoding="utf-8")
            for tree in cc.DEFAULT_PRODUCT_TREE_ROOTS:
                (root / tree / "__init__.py").parent.mkdir(parents=True, exist_ok=True)
                (root / tree / "__init__.py").write_text(f"# {tree}\n", encoding="utf-8")
            cat = (
                root
                / "docs/milestones/007-cli-operator-usability/tools/live-cli-session-runner/catalogs"
            )
            cat.mkdir(parents=True)
            catalog_path = cat / "m007-continuity.yaml"
            catalog_path.write_text("id: m007-continuity\n", encoding="utf-8")
            runner = (
                root
                / "docs/milestones/007-cli-operator-usability/tools/live-cli-session-runner/session_runner.py"
            )
            runner.parent.mkdir(parents=True, exist_ok=True)
            runner.write_text("# runner\n", encoding="utf-8")
            (runner.parent / "continuity_contract.py").write_text("# c\n", encoding="utf-8")
            recorded = cc.collect_identity_bundle(repo_root=root, catalog_path=catalog_path)
            recorded["metrics_ui_required"] = True
            recorded["metrics_ui"] = {"commit": "AAA", "worktree_state": "clean"}
            session = root / "session"
            session.mkdir()
            (session / "result.json").write_text(
                json.dumps({"result": "incomplete", "continuity": {"identity_recorded": recorded}}),
                encoding="utf-8",
            )
            # No metrics_ui / metrics_ui_repo supplied — must fail closed, not reuse recorded.
            out = cc.validate_session_against_tree(
                session, repo_root=root, catalog_path=catalog_path
            )
            self.assertFalse(out["ok"], out)
            self.assertIn("metrics_ui", out["reason"])



if __name__ == "__main__":
    unittest.main()


class DirtyMetricsUiIdentityRegressionTests(unittest.TestCase):
    def test_dirty_a_to_b_without_linked_pr_fails(self) -> None:
        product = _full_product_map("p")
        rec = {
            "catalog_sha256": "a",
            "runner_sha256": "b",
            "continuity_contract_sha256": "c",
            "product_sha256": product,
            "metrics_ui_required": True,
            "metrics_ui": {
                "commit": "SAME",
                "worktree_state": "dirty",
                "diff_identity": "DIFF-A",
                "linked_pr": None,
            },
        }
        cur = {
            "catalog_sha256": "a",
            "runner_sha256": "b",
            "continuity_contract_sha256": "c",
            "product_sha256": product,
            "metrics_ui_required": True,
            "metrics_ui": {
                "commit": "SAME",
                "worktree_state": "dirty",
                "diff_identity": "DIFF-B",
                "linked_pr": None,
            },
        }
        ok, reason = cc.finalize_evidence_freshness(rec, cur)
        self.assertFalse(ok, reason)
        self.assertIn("diff_identity", reason)

    def test_unchanged_dirty_round_trip_ok(self) -> None:
        product = _full_product_map("p")
        bundle = {
            "catalog_sha256": "a",
            "runner_sha256": "b",
            "continuity_contract_sha256": "c",
            "product_sha256": product,
            "metrics_ui_required": True,
            "metrics_ui": {
                "commit": "SAME",
                "worktree_state": "dirty",
                "diff_identity": "DIFF-STABLE",
                "linked_pr": None,
            },
        }
        ok, reason = cc.finalize_evidence_freshness(bundle, bundle)
        self.assertTrue(ok, reason)

    def test_collect_git_identity_binds_untracked_bytes(self) -> None:
        import subprocess

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "t@example.com"],
                cwd=repo,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "t"],
                cwd=repo,
                check=True,
                capture_output=True,
            )
            (repo / "README").write_text("x\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "init"],
                cwd=repo,
                check=True,
                capture_output=True,
            )
            untracked = repo / "untracked.txt"
            untracked.write_text("A\n", encoding="utf-8")
            id_a = cc.collect_git_identity(repo)
            self.assertIsNotNone(id_a)
            assert id_a is not None
            self.assertEqual(id_a.get("worktree_state"), "dirty")
            self.assertTrue(id_a.get("diff_identity"))
            untracked.write_text("B\n", encoding="utf-8")
            id_b = cc.collect_git_identity(repo)
            assert id_b is not None
            self.assertNotEqual(
                id_a.get("diff_identity"),
                id_b.get("diff_identity"),
                "changed untracked bytes must change diff_identity",
            )
            # Round-trip finalize with collected identities
            product = _full_product_map("p")
            rec = {
                "catalog_sha256": "a",
                "runner_sha256": "b",
                "continuity_contract_sha256": "c",
                "product_sha256": product,
                "metrics_ui_required": True,
                "metrics_ui": id_a,
            }
            cur = {
                "catalog_sha256": "a",
                "runner_sha256": "b",
                "continuity_contract_sha256": "c",
                "product_sha256": product,
                "metrics_ui_required": True,
                "metrics_ui": id_b,
            }
            ok, reason = cc.finalize_evidence_freshness(rec, cur)
            self.assertFalse(ok)
            self.assertIn("diff_identity", reason)


class SharedGitIdentityCollectorTests(unittest.TestCase):
    """Session recording and post-hoc finalization must share one identity algorithm."""

    def test_session_runner_git_identity_delegates_to_collect_git_identity(self) -> None:
        import importlib.util

        runner_path = (
            Path(__file__).resolve().parents[2]
            / "docs/milestones/007-cli-operator-usability/tools/live-cli-session-runner/session_runner.py"
        )
        # Source-level: _git_identity must call collect_git_identity, not reimplement.
        src = runner_path.read_text(encoding="utf-8")
        self.assertIn("identity = collect_git_identity(repo)", src)
        # Body must not re-hash untracked files inline.
        body = src.split("def _git_identity", 1)[1].split("\ndef ", 1)[0]
        self.assertNotIn("untracked-symlink", body)
        self.assertNotIn("git status --porcelain", body)

    def test_record_and_posthoc_collectors_match_on_dirty_tree(self) -> None:
        import importlib.util
        import subprocess

        runner_path = (
            Path(__file__).resolve().parents[2]
            / "docs/milestones/007-cli-operator-usability/tools/live-cli-session-runner/session_runner.py"
        )
        name = "live_cli_session_runner_identity"
        import sys

        sys.modules.pop(name, None)
        spec = importlib.util.spec_from_file_location(name, runner_path)
        assert spec and spec.loader
        runner = importlib.util.module_from_spec(spec)
        sys.modules[name] = runner
        spec.loader.exec_module(runner)

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "t@example.com"],
                cwd=repo,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "t"],
                cwd=repo,
                check=True,
                capture_output=True,
            )
            (repo / "README").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "init"],
                cwd=repo,
                check=True,
                capture_output=True,
            )
            (repo / "dirty.txt").write_text("A\n", encoding="utf-8")
            recorded = runner._git_identity(repo)
            posthoc = cc.collect_git_identity(repo)
            self.assertIsNotNone(posthoc)
            assert posthoc is not None
            self.assertEqual(recorded.get("diff_identity"), posthoc.get("diff_identity"))
            self.assertEqual(recorded.get("commit"), posthoc.get("commit"))
            self.assertEqual(recorded.get("worktree_state"), "dirty")
            # Change untracked bytes — both collectors must move together.
            (repo / "dirty.txt").write_text("B\n", encoding="utf-8")
            recorded_b = runner._git_identity(repo)
            posthoc_b = cc.collect_git_identity(repo)
            assert posthoc_b is not None
            self.assertEqual(recorded_b.get("diff_identity"), posthoc_b.get("diff_identity"))
            self.assertNotEqual(recorded.get("diff_identity"), recorded_b.get("diff_identity"))


class UntrackedGitMaterialIdentityTests(unittest.TestCase):
    def _init_repo(self, repo: Path) -> None:
        import subprocess

        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "t@example.com"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "t"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        (repo / "README").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

    def _assert_path_change_invalidates_identity(self, name: str) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self._init_repo(repo)
            untracked = repo / name
            untracked.write_bytes(b"A\n")

            id_a = cc.collect_git_identity(repo)
            self.assertIsNotNone(id_a)
            assert id_a is not None
            self.assertIn(name, id_a.get("untracked_files", []))
            id_a2 = cc.collect_git_identity(repo)
            assert id_a2 is not None
            self.assertEqual(
                id_a.get("diff_identity"),
                id_a2.get("diff_identity"),
                "unchanged path/content must have stable dirty identity",
            )

            untracked.write_bytes(b"B\n")
            id_b = cc.collect_git_identity(repo)
            assert id_b is not None
            self.assertNotEqual(
                id_a.get("diff_identity"),
                id_b.get("diff_identity"),
                "changed bytes must alter dirty identity even for quoted paths",
            )
            id_b2 = cc.collect_git_identity(repo)
            assert id_b2 is not None
            self.assertEqual(id_b.get("diff_identity"), id_b2.get("diff_identity"))

            product = _full_product_map("p")
            rec = {
                "catalog_sha256": "a",
                "runner_sha256": "b",
                "continuity_contract_sha256": "c",
                "product_sha256": product,
                "metrics_ui_required": True,
                "metrics_ui": id_a,
            }
            cur = {
                "catalog_sha256": "a",
                "runner_sha256": "b",
                "continuity_contract_sha256": "c",
                "product_sha256": product,
                "metrics_ui_required": True,
                "metrics_ui": id_b,
            }
            ok, reason = cc.finalize_evidence_freshness(rec, cur)
            self.assertFalse(ok, reason)
            self.assertIn("diff_identity", reason)

    def test_unicode_untracked_path_binds_actual_path_bytes(self) -> None:
        self._assert_path_change_invalidates_identity("café.txt")

    def test_embedded_newline_untracked_path_binds_actual_path_bytes(self) -> None:
        self._assert_path_change_invalidates_identity("line\nbreak.txt")

    def test_symlink_target_change_invalidates_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self._init_repo(repo)
            link = repo / "link"
            link.symlink_to("target-A")
            id_a = cc.collect_git_identity(repo)
            assert id_a is not None
            link.unlink()
            link.symlink_to("target-B")
            id_b = cc.collect_git_identity(repo)
            assert id_b is not None
            self.assertNotEqual(
                id_a.get("diff_identity"),
                id_b.get("diff_identity"),
                "symlink target change must alter dirty identity",
            )
            # Unchanged round-trip
            id_b2 = cc.collect_git_identity(repo)
            assert id_b2 is not None
            self.assertEqual(id_b.get("diff_identity"), id_b2.get("diff_identity"))

    def test_executable_bit_change_invalidates_identity(self) -> None:
        import os
        import stat as statmod

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self._init_repo(repo)
            tool = repo / "tool.sh"
            tool.write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
            tool.chmod(0o644)
            id_a = cc.collect_git_identity(repo)
            assert id_a is not None
            tool.chmod(0o755)
            id_b = cc.collect_git_identity(repo)
            assert id_b is not None
            self.assertNotEqual(
                id_a.get("diff_identity"),
                id_b.get("diff_identity"),
                "executable bit change must alter dirty identity",
            )
            id_b2 = cc.collect_git_identity(repo)
            assert id_b2 is not None
            self.assertEqual(id_b.get("diff_identity"), id_b2.get("diff_identity"))
            # Finalize rejects A->B
            product = _full_product_map("p")
            rec = {
                "catalog_sha256": "a",
                "runner_sha256": "b",
                "continuity_contract_sha256": "c",
                "product_sha256": product,
                "metrics_ui_required": True,
                "metrics_ui": id_a,
            }
            cur = {
                "catalog_sha256": "a",
                "runner_sha256": "b",
                "continuity_contract_sha256": "c",
                "product_sha256": product,
                "metrics_ui_required": True,
                "metrics_ui": id_b,
            }
            ok, reason = cc.finalize_evidence_freshness(rec, cur)
            self.assertFalse(ok)
            self.assertIn("diff_identity", reason)
