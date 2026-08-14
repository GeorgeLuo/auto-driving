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
        # Mutating the claim map away from frozen authority must fail closed.
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
        self.assertIn("frozen", str(ctx.exception).lower())

    def test_full_audit_pass(self) -> None:
        result = self.validate_audit.run_audit(repo_root=ROOT)
        self.assertEqual(result["report"]["result"], "pass")
        self.assertEqual(result["report"]["sequences"]["count"], 10)
        self.assertGreaterEqual(result["report"]["leaves"]["count"], 40)
        self.assertIn("Deferred", result["rollup"])
        self.assertIn("Coverage residuals", result["rollup"])
        self.assertIn("unmeasured", result["rollup"])
        self.assertIn("not_applicable", result["rollup"])

    def test_catalog_template_must_match_frozen_authority(self) -> None:
        catalog_path = TOOL / "us88_catalog.json"
        data = json.loads(catalog_path.read_text(encoding="utf-8"))
        for entry in data["entries"]:
            if entry["id"] == "US-01":
                entry["required_command_templates"] = [
                    ["vehicles", "info", "memory", "--id", "{vehicle_id}"],
                ]
        bad = TOOL / "_tmp_us01_rewrite.json"
        try:
            payload = json.dumps(data, indent=2, sort_keys=True) + "\n"
            bad.write_text(payload, encoding="utf-8")
            import hashlib

            digest = hashlib.sha256(payload.encode()).hexdigest()
            anchor = TOOL / "_tmp_us01_rewrite.sha256"
            anchor.write_text(digest + "\n", encoding="utf-8")
            with self.assertRaises(self.validate_audit.AuditError) as ctx:
                self.validate_audit.load_catalog(bad, anchor_path=anchor)
            self.assertIn("frozen", str(ctx.exception).lower())
        finally:
            for path in (bad, TOOL / "_tmp_us01_rewrite.sha256"):
                if path.exists():
                    path.unlink()

    def test_wrong_source_commit_fails(self) -> None:
        sequences = json.loads(
            (TOOL / "sequence_registry.json").read_text(encoding="utf-8")
        )
        claim_map = json.loads((TOOL / "claim_map.json").read_text(encoding="utf-8"))
        for row in sequences["sequences"]:
            if row["id"] == "US-02":
                row["evidence"]["source_commit"] = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
        with self.assertRaises(self.validate_audit.AuditError) as ctx:
            self.validate_audit.validate_semantic_cite(
                sequences=sequences,
                claim_map=claim_map,
                repo_root=ROOT,
            )
        self.assertIn("source_commit", str(ctx.exception))

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
        msg = str(ctx.exception).lower()
        self.assertTrue(
            "cleanup must equal" in msg
            or "not in inventory" in msg
            or "argv invalid" in msg
            or "missing required cleanup" in msg,
            msg,
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

    def test_wrong_document_schema_fails(self) -> None:
        overlay = json.loads((TOOL / "leaf_overlay.json").read_text(encoding="utf-8"))
        overlay["schema"] = "wrong_schema"
        with self.assertRaises(self.validate_audit.AuditError):
            self.validate_audit._require_document_schema(
                overlay, "m007_leaf_overlay_v1", where="leaf_overlay"
            )

    def test_catalog_wrong_source_url_fails(self) -> None:
        catalog_path = TOOL / "us88_catalog.json"
        data = json.loads(catalog_path.read_text(encoding="utf-8"))
        data["source"]["url"] = "https://example.invalid/not-88"
        data["source"]["comment_id"] = 1
        data["source"]["title"] = ""
        bad = TOOL / "_tmp_bad_source_catalog.json"
        try:
            bad.write_text(json.dumps(data), encoding="utf-8")
            # Keep anchor matching bad file content so only source identity fails.
            import hashlib

            digest = hashlib.sha256(bad.read_bytes()).hexdigest()
            anchor = TOOL / "_tmp_bad_source_catalog.sha256"
            anchor.write_text(digest + "\n", encoding="utf-8")
            with self.assertRaises(self.validate_audit.AuditError) as ctx:
                self.validate_audit.load_catalog(bad, anchor_path=anchor)
            self.assertIn("canonical", str(ctx.exception).lower())
        finally:
            for path in (bad, TOOL / "_tmp_bad_source_catalog.sha256"):
                if path.exists():
                    path.unlink()

    def test_primary_confirmation_mismatch_fails(self) -> None:
        catalog = self.validate_audit.load_catalog()
        cat_sha = self.validate_audit.catalog_digest()
        sequences = json.loads(
            (TOOL / "sequence_registry.json").read_text(encoding="utf-8")
        )
        sequences["sequences"][0]["primary_confirmation"] = "arbitrary confirmation"
        with self.assertRaises(self.validate_audit.AuditError) as ctx:
            self.validate_audit.validate_sequences(
                catalog=catalog,
                catalog_sha=cat_sha,
                sequences=sequences,
                leaf_ids=set(self.parser_walk.public_leaf_ids()),
            )
        self.assertIn("primary_confirmation", str(ctx.exception))

    def test_absolute_cite_path_fails(self) -> None:
        # Absolute path rejection is enforced inside cite validation; coordinated
        # claim-map rewrites are also rejected by frozen-authority equality.
        sequences = json.loads(
            (TOOL / "sequence_registry.json").read_text(encoding="utf-8")
        )
        claim_map = json.loads((TOOL / "claim_map.json").read_text(encoding="utf-8"))
        abs_path = str(
            (
                ROOT
                / "docs/milestones/007-cli-operator-usability/evidence/"
                "live-cli-acceptance/result.json"
            ).resolve()
        )
        claim_map["claims"]["us01_us02_live_acceptance"]["paths"] = [abs_path]
        import hashlib

        digest = hashlib.sha256(Path(abs_path).read_bytes()).hexdigest()
        for row in sequences["sequences"]:
            if row["id"] in {"US-01", "US-02"}:
                row["evidence"]["digests"] = {abs_path: digest}
        with self.assertRaises(self.validate_audit.AuditError) as ctx:
            self.validate_audit.validate_semantic_cite(
                sequences=sequences,
                claim_map=claim_map,
                repo_root=ROOT,
            )
        msg = str(ctx.exception).lower()
        self.assertTrue(
            "frozen" in msg or "repository-relative" in msg,
            msg,
        )

    def test_command_template_must_match_catalog(self) -> None:
        catalog = self.validate_audit.load_catalog()
        cat_sha = self.validate_audit.catalog_digest()
        sequences = json.loads(
            (TOOL / "sequence_registry.json").read_text(encoding="utf-8")
        )
        for row in sequences["sequences"]:
            if row["id"] == "US-03":
                row["commands"] = [
                    ["vehicles", "status", "--id", "{vehicle_id}"],
                ]
        with self.assertRaises(self.validate_audit.AuditError) as ctx:
            self.validate_audit.validate_sequences(
                catalog=catalog,
                catalog_sha=cat_sha,
                sequences=sequences,
                leaf_ids=set(self.parser_walk.public_leaf_ids()),
            )
        self.assertIn("required_command_templates", str(ctx.exception))

    def test_live_residual_bogus_disposition_fails(self) -> None:
        sequences = json.loads(
            (TOOL / "sequence_registry.json").read_text(encoding="utf-8")
        )
        overlay = json.loads((TOOL / "leaf_overlay.json").read_text(encoding="utf-8"))
        residuals = json.loads(
            (TOOL / "live_residuals.json").read_text(encoding="utf-8")
        )
        residuals["findings"][0]["owner"] = ""
        residuals["findings"][0]["disposition"] = "bogus"
        with self.assertRaises(self.validate_audit.AuditError):
            self.validate_audit.validate_live_residuals(
                sequences=sequences,
                overlay=overlay,
                residuals=residuals["findings"],
            )


if __name__ == "__main__":
    unittest.main()
