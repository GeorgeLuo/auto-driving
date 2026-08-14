from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "docs"
    / "milestones"
    / "007-cli-operator-usability"
    / "tools"
    / "cli-surface-audit"
)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class CliSurfaceAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        sys.path.insert(0, str(ROOT))
        sys.path.insert(0, str(TOOL))
        cls.parser_walk = _load("parser_walk", TOOL / "parser_walk.py")
        cls.argv_validate = _load("argv_validate", TOOL / "argv_validate.py")
        cls.validate_audit = _load("validate_audit", TOOL / "validate_audit.py")

    def test_leaf_membership_matches_parser(self) -> None:
        inventory = json.loads((TOOL / "leaf_inventory.json").read_text(encoding="utf-8"))
        ids = self.parser_walk.public_leaf_ids()
        recorded = [row["leaf_id"] for row in inventory["leaves"]]
        self.assertEqual(sorted(ids), sorted(recorded))
        self.assertGreaterEqual(len(ids), 20)

    def test_overlay_omission_fails(self) -> None:
        inventory = json.loads((TOOL / "leaf_inventory.json").read_text(encoding="utf-8"))
        overlay = json.loads((TOOL / "leaf_overlay.json").read_text(encoding="utf-8"))
        leaf_id = next(iter(overlay["leaves"]))
        del overlay["leaves"][leaf_id]["safety_class"]
        with self.assertRaises(self.validate_audit.AuditError):
            self.validate_audit.validate_overlay(
                leaf_ids=[row["leaf_id"] for row in inventory["leaves"]],
                overlay=overlay,
            )

    def test_unknown_flag_fails_argv_validation(self) -> None:
        receipt = self.argv_validate.validate_argv(
            [
                "vehicles",
                "automation",
                "run",
                "--id",
                "chase-sim-chaser",
                "--bogus",
            ],
            template_id="bad-flag",
        )
        self.assertFalse(receipt.ok)

    def test_help_with_extra_args_fails(self) -> None:
        receipt = self.argv_validate.validate_argv(
            ["vehicles", "automation", "run", "--help", "--bogus"],
            template_id="help-extra",
        )
        self.assertFalse(receipt.ok)
        self.assertIn("help flag", receipt.reason)

    def test_executed_mode_without_package_fails(self) -> None:
        sequences = json.loads(
            (TOOL / "sequence_registry.json").read_text(encoding="utf-8")
        )
        claim_map = json.loads((TOOL / "claim_map.json").read_text(encoding="utf-8"))
        for row in sequences["sequences"]:
            if row["id"] == "US-02":
                row["evidence"] = {"evidence_mode": "executed"}
        with self.assertRaises(self.validate_audit.AuditError) as ctx:
            self.validate_audit.validate_semantic_cite(
                sequences=sequences,
                claim_map=claim_map,
                repo_root=ROOT,
            )
        self.assertIn("executed", str(ctx.exception).lower())

    def test_cite_claim_map_mismatch_fails(self) -> None:
        sequences = json.loads(
            (TOOL / "sequence_registry.json").read_text(encoding="utf-8")
        )
        claim_map = json.loads((TOOL / "claim_map.json").read_text(encoding="utf-8"))
        for row in sequences["sequences"]:
            if row["id"] == "US-01":
                row["evidence"]["claim_map_id"] = "continuity_offline_perception"
                row["evidence"]["source_pr"] = 100
        with self.assertRaises(self.validate_audit.AuditError) as ctx:
            self.validate_audit.validate_semantic_cite(
                sequences=sequences,
                claim_map=claim_map,
                repo_root=ROOT,
            )
        self.assertIn("binding", str(ctx.exception).lower())

    def test_missing_digest_fails(self) -> None:
        sequences = json.loads(
            (TOOL / "sequence_registry.json").read_text(encoding="utf-8")
        )
        claim_map = json.loads((TOOL / "claim_map.json").read_text(encoding="utf-8"))
        for row in sequences["sequences"]:
            if row["id"] == "US-01":
                row["evidence"]["digests"] = {}
        with self.assertRaises(self.validate_audit.AuditError) as ctx:
            self.validate_audit.validate_semantic_cite(
                sequences=sequences,
                claim_map=claim_map,
                repo_root=ROOT,
            )
        self.assertIn("digest", str(ctx.exception).lower())

    def test_empty_prerequisite_fails(self) -> None:
        catalog = self.validate_audit.load_catalog()
        cat_sha = self.validate_audit.catalog_digest()
        sequences = json.loads(
            (TOOL / "sequence_registry.json").read_text(encoding="utf-8")
        )
        sequences["sequences"][0]["prerequisites"] = "   "
        with self.assertRaises(self.validate_audit.AuditError):
            self.validate_audit.validate_sequences(
                catalog=catalog,
                catalog_sha=cat_sha,
                sequences=sequences,
                leaf_ids=set(self.parser_walk.public_leaf_ids()),
            )

    def test_catalog_swap_fails(self) -> None:
        catalog = self.validate_audit.load_catalog()
        cat_sha = self.validate_audit.catalog_digest()
        sequences = json.loads(
            (TOOL / "sequence_registry.json").read_text(encoding="utf-8")
        )
        # Swap US-06 and US-07 operator questions while keeping ids.
        by_id = {row["id"]: row for row in sequences["sequences"]}
        q6 = by_id["US-06"]["operator_question"]
        by_id["US-06"]["operator_question"] = by_id["US-07"]["operator_question"]
        by_id["US-07"]["operator_question"] = q6
        with self.assertRaises(self.validate_audit.AuditError) as ctx:
            self.validate_audit.validate_sequences(
                catalog=catalog,
                catalog_sha=cat_sha,
                sequences=sequences,
                leaf_ids=set(self.parser_walk.public_leaf_ids()),
            )
        self.assertIn("does not match catalog", str(ctx.exception))

    def test_machine_only_incomplete_cite_rejected(self) -> None:
        sequences = json.loads(
            (TOOL / "sequence_registry.json").read_text(encoding="utf-8")
        )
        claim_map = json.loads((TOOL / "claim_map.json").read_text(encoding="utf-8"))
        # Point US-04 claim paths at incomplete machine-only package with real digest.
        bad_path = (
            "docs/milestones/007-cli-operator-usability/evidence/"
            "cli-scenario-continuity/machine-only-session/result.json"
        )
        import hashlib

        digest = hashlib.sha256((ROOT / bad_path).read_bytes()).hexdigest()
        claim_map["claims"]["continuity_live_config_swap"]["paths"] = [bad_path]
        for row in sequences["sequences"]:
            if row["id"] == "US-04":
                row["evidence"]["digests"] = {bad_path: digest}
        with self.assertRaises(self.validate_audit.AuditError) as ctx:
            self.validate_audit.validate_semantic_cite(
                sequences=sequences,
                claim_map=claim_map,
                repo_root=ROOT,
            )
        self.assertIn("predicate failed", str(ctx.exception).lower())

    def test_full_audit_pass(self) -> None:
        result = self.validate_audit.run_audit(repo_root=ROOT)
        self.assertEqual(result["report"]["result"], "pass")
        self.assertEqual(result["report"]["sequences"]["count"], 10)
        self.assertIn("Deferred", result["rollup"])
        self.assertIn("Coverage residuals", result["rollup"])
        self.assertIn("unmeasured", result["rollup"])
        self.assertIn("not_applicable", result["rollup"])

    def test_scalar_safety_not_applicable_fails(self) -> None:
        inventory = json.loads((TOOL / "leaf_inventory.json").read_text(encoding="utf-8"))
        overlay = json.loads((TOOL / "leaf_overlay.json").read_text(encoding="utf-8"))
        leaf_id = next(iter(overlay["leaves"]))
        overlay["leaves"][leaf_id]["safety_class"] = "not_applicable"
        with self.assertRaises(self.validate_audit.AuditError) as ctx:
            self.validate_audit.validate_overlay(
                leaf_ids=[row["leaf_id"] for row in inventory["leaves"]],
                overlay=overlay,
            )
        self.assertIn("object form", str(ctx.exception))

    def test_cleanup_non_terminal_leaf_fails(self) -> None:
        catalog = self.validate_audit.load_catalog()
        cat_sha = self.validate_audit.catalog_digest()
        sequences = json.loads(
            (TOOL / "sequence_registry.json").read_text(encoding="utf-8")
        )
        for row in sequences["sequences"]:
            if row["id"] == "US-02":
                row["cleanup"] = [["vehicles", "automation", "--help"]]
        with self.assertRaises(self.validate_audit.AuditError) as ctx:
            self.validate_audit.validate_sequences(
                catalog=catalog,
                catalog_sha=cat_sha,
                sequences=sequences,
                leaf_ids=set(self.parser_walk.public_leaf_ids()),
            )
        self.assertTrue(
            "not in inventory" in str(ctx.exception)
            or "argv invalid" in str(ctx.exception)
        )

    def test_catalog_missing_source_fails(self) -> None:
        catalog_path = TOOL / "us88_catalog.json"
        data = json.loads(catalog_path.read_text(encoding="utf-8"))
        del data["source"]
        bad = TOOL / "_tmp_bad_catalog.json"
        try:
            bad.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(self.validate_audit.AuditError) as ctx:
                self.validate_audit.load_catalog(bad, anchor_path=TOOL / "us88_catalog.sha256")
            self.assertIn("source", str(ctx.exception).lower())
        finally:
            if bad.exists():
                bad.unlink()

    def test_empty_inventory_help_fails(self) -> None:
        inventory = json.loads((TOOL / "leaf_inventory.json").read_text(encoding="utf-8"))
        inventory["leaves"][0]["help"] = "   "
        with self.assertRaises(self.validate_audit.AuditError):
            self.validate_audit.validate_leaf_inventory_document(inventory)

    def test_help_walk_detects_directionality(self) -> None:
        report = self.validate_audit.help_drift_report()
        self.assertIn(report["status"], {"ok", "drift_reported"})
        self.assertGreater(report["help_leaf_count"], 0)
        self.assertIsInstance(report["missing_from_help"], list)
        self.assertIsInstance(report["extra_in_help"], list)


if __name__ == "__main__":
    unittest.main()
