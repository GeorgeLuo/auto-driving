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
        leaf_ids = [row["leaf_id"] for row in inventory["leaves"]]
        # Prefer an action leaf so meta-only constraints do not mask omissions.
        leaf_id = next(
            lid
            for lid, row in overlay["leaves"].items()
            if row.get("kind") == "action"
        )
        required = self.validate_audit.REQUIRED_OVERLAY_FIELDS + ("supports_json", "kind")
        for field in required:
            mutated = json.loads(json.dumps(overlay))
            del mutated["leaves"][leaf_id][field]
            with self.assertRaises(self.validate_audit.AuditError):
                self.validate_audit.validate_overlay(
                    leaf_ids=leaf_ids,
                    overlay=mutated,
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
        self.assertEqual(result["report"]["leaves"]["meta_count"], 10)
        self.assertEqual(
            result["report"]["leaves"]["action_count"]
            + result["report"]["leaves"]["meta_count"],
            result["report"]["leaves"]["count"],
        )
        self.assertEqual(result["report"]["help_drift"]["status"], "ok")
        self.assertEqual(
            result["report"]["help_drift"]["action_leaf_count"],
            result["report"]["leaves"]["action_count"],
        )
        self.assertEqual(result["report"]["help_drift"]["missing_from_help"], [])
        self.assertIn("Deferred", result["rollup"])
        self.assertIn("Coverage residuals", result["rollup"])
        self.assertIn("unmeasured", result["rollup"])
        self.assertIn("not_applicable", result["rollup"])
        self.assertIn("action=32", result["rollup"])
        self.assertIn("meta=10", result["rollup"])

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
        claim_map["claims"]["us01_help_discovery"]["paths"] = [abs_path]
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

    def test_help_meta_kind_tagged(self) -> None:
        inventory = json.loads((TOOL / "leaf_inventory.json").read_text(encoding="utf-8"))
        meta = [row for row in inventory["leaves"] if row["kind"] == "meta"]
        action = [row for row in inventory["leaves"] if row["kind"] == "action"]
        self.assertEqual(len(meta), 10)
        self.assertTrue(all(row["tokens"][-1] == "help" for row in meta))
        self.assertTrue(all(row["tokens"][-1] != "help" for row in action))
        walk = self.parser_walk.walk_leaves(
            __import__("cli.automa_cli.app", fromlist=["build_parser"]).build_parser()
        )
        self.assertEqual(
            {leaf.leaf_id: leaf.kind for leaf in walk},
            {row["leaf_id"]: row["kind"] for row in inventory["leaves"]},
        )

    def test_supports_json_parser_parity_mutation_fails(self) -> None:
        inventory = json.loads((TOOL / "leaf_inventory.json").read_text(encoding="utf-8"))
        overlay = json.loads((TOOL / "leaf_overlay.json").read_text(encoding="utf-8"))
        leaf_ids = [row["leaf_id"] for row in inventory["leaves"]]
        leaf_id = "vehicles.memory.check"
        self.assertTrue(overlay["leaves"][leaf_id]["supports_json"])
        overlay["leaves"][leaf_id]["supports_json"] = False
        overlay["leaves"][leaf_id]["json_capability"] = (
            self.validate_audit.JSON_ABSENT_SENTENCE
        )
        with self.assertRaises(self.validate_audit.AuditError) as ctx:
            self.validate_audit.validate_overlay(leaf_ids=leaf_ids, overlay=overlay)
        self.assertIn("argparse", str(ctx.exception).lower())

    def test_deterministic_validation_class_enforced(self) -> None:
        inventory = json.loads((TOOL / "leaf_inventory.json").read_text(encoding="utf-8"))
        overlay = json.loads((TOOL / "leaf_overlay.json").read_text(encoding="utf-8"))
        leaf_ids = [row["leaf_id"] for row in inventory["leaves"]]
        overlay["leaves"]["vehicles.memory.replay"]["validation_class"] = "live"
        with self.assertRaises(self.validate_audit.AuditError) as ctx:
            self.validate_audit.validate_overlay(leaf_ids=leaf_ids, overlay=overlay)
        self.assertIn("deterministic", str(ctx.exception).lower())

    def test_catalog_anchor_and_delta_required(self) -> None:
        catalog_path = TOOL / "us88_catalog.json"
        data = json.loads(catalog_path.read_text(encoding="utf-8"))
        for entry in data["entries"]:
            if entry["id"] == "US-03":
                entry["command_deltas"] = []
                entry["source_anchor"] = "### US-99 — invented"
        bad = TOOL / "_tmp_anchor_catalog.json"
        try:
            import hashlib

            payload = json.dumps(data, indent=2, sort_keys=True) + "\n"
            bad.write_text(payload, encoding="utf-8")
            digest = hashlib.sha256(payload.encode()).hexdigest()
            anchor = TOOL / "_tmp_anchor_catalog.sha256"
            anchor.write_text(digest + "\n", encoding="utf-8")
            with self.assertRaises(self.validate_audit.AuditError) as ctx:
                self.validate_audit.load_catalog(bad, anchor_path=anchor)
            self.assertIn("frozen", str(ctx.exception).lower())
        finally:
            for path in (bad, TOOL / "_tmp_anchor_catalog.sha256"):
                if path.exists():
                    path.unlink()

    def test_overlay_not_applicable_form_matrix(self) -> None:
        inventory = json.loads((TOOL / "leaf_inventory.json").read_text(encoding="utf-8"))
        overlay = json.loads((TOOL / "leaf_overlay.json").read_text(encoding="utf-8"))
        leaf_ids = [row["leaf_id"] for row in inventory["leaves"]]
        leaf_id = next(
            lid
            for lid, row in overlay["leaves"].items()
            if row.get("kind") == "action"
        )
        # Scalar not_applicable on safety_class is already covered; also reject
        # empty prerequisites and empty side_effects.
        for field in ("prerequisites", "side_effects"):
            mutated = json.loads(json.dumps(overlay))
            mutated["leaves"][leaf_id][field] = ""
            with self.assertRaises(self.validate_audit.AuditError):
                self.validate_audit.validate_overlay(leaf_ids=leaf_ids, overlay=mutated)
            mutated = json.loads(json.dumps(overlay))
            mutated["leaves"][leaf_id][field] = {
                "value": "not_applicable",
                "reason": "",
            }
            with self.assertRaises(self.validate_audit.AuditError):
                self.validate_audit.validate_overlay(leaf_ids=leaf_ids, overlay=mutated)

    def test_help_drift_excludes_meta(self) -> None:
        report = self.validate_audit.help_drift_report()
        self.assertEqual(report["meta_leaf_count"], 10)
        self.assertNotIn("help", report.get("missing_from_help", []))
        self.assertTrue(
            all(not mid.endswith(".help") and mid != "help" for mid in report["missing_from_help"])
        )

    def test_json_capability_must_equal_argparse_sentence(self) -> None:
        inventory = json.loads((TOOL / "leaf_inventory.json").read_text(encoding="utf-8"))
        overlay = json.loads((TOOL / "leaf_overlay.json").read_text(encoding="utf-8"))
        leaf_ids = [row["leaf_id"] for row in inventory["leaves"]]
        leaf_id = "vehicles.memory.check"
        self.assertEqual(
            overlay["leaves"][leaf_id]["json_capability"],
            self.validate_audit.JSON_CAPABLE_SENTENCE,
        )
        overlay["leaves"][leaf_id]["json_capability"] = (
            self.validate_audit.JSON_CAPABLE_SENTENCE + " However, --json is unavailable."
        )
        with self.assertRaises(self.validate_audit.AuditError) as ctx:
            self.validate_audit.validate_overlay(leaf_ids=leaf_ids, overlay=overlay)
        self.assertIn("json_capability", str(ctx.exception))

    def test_output_contract_cannot_restate_or_contradict_json(self) -> None:
        inventory = json.loads((TOOL / "leaf_inventory.json").read_text(encoding="utf-8"))
        overlay = json.loads((TOOL / "leaf_overlay.json").read_text(encoding="utf-8"))
        leaf_ids = [row["leaf_id"] for row in inventory["leaves"]]
        check = overlay["leaves"]["vehicles.memory.check"]
        run = overlay["leaves"]["vehicles.automation.run"]
        self.assertEqual(
            check["json_capability"], self.validate_audit.JSON_CAPABLE_SENTENCE
        )
        self.assertEqual(
            run["json_capability"], self.validate_audit.JSON_ABSENT_SENTENCE
        )
        check["output_contract"] = (
            "Human summary. Supports --json. However, --json is unavailable."
        )
        with self.assertRaises(self.validate_audit.AuditError) as ctx:
            self.validate_audit.validate_overlay(leaf_ids=leaf_ids, overlay=overlay)
        self.assertIn("must not mention --json", str(ctx.exception))
        check["output_contract"] = "Memory check PASS/FAIL."
        run["output_contract"] = (
            "Human summary. No --json flag on this leaf. Even so, --json is available."
        )
        with self.assertRaises(self.validate_audit.AuditError) as ctx:
            self.validate_audit.validate_overlay(leaf_ids=leaf_ids, overlay=overlay)
        self.assertIn("must not mention --json", str(ctx.exception))

    def test_us06_source_requires_two_trial_shape(self) -> None:
        catalog = json.loads((TOOL / "us88_catalog.json").read_text(encoding="utf-8"))
        us06 = next(row for row in catalog["entries"] if row["id"] == "US-06")
        cmds = us06["required_command_templates"]
        checks = [
            cmd
            for cmd in cmds
            if cmd[:3] == ["vehicles", "memory", "check"] and "--record" in cmd
        ]
        self.assertGreaterEqual(len(checks), 2)
        disable_idx = next(
            i
            for i, cmd in enumerate(cmds)
            if cmd[:3] == ["vehicles", "perception", "disable"]
            and "motion_tracks" in cmd
        )
        self.assertTrue(
            any(cmd[:3] == ["vehicles", "automation", "stop"] for cmd in cmds[:disable_idx])
        )
        after = cmds[disable_idx + 1 :]
        self.assertTrue(any(cmd[:3] == ["vehicles", "automation", "run"] for cmd in after))
        self.assertTrue(any(cmd[:3] == ["vehicles", "memory", "check"] for cmd in after))
        # Dropping the baseline trial must fail the source-shape owner, even if
        # catalog and frozen constants were edited together.
        us06["required_command_templates"] = cmds[disable_idx:]
        with self.assertRaises(self.validate_audit.AuditError) as ctx:
            self.validate_audit.validate_source_command_shapes(catalog)
        self.assertIn("US-06", str(ctx.exception))
        # Coordinated omission: keep both check tokens, drop US-05 setup/run.
        us06["required_command_templates"] = cmds[3:]
        with self.assertRaises(self.validate_audit.AuditError) as ctx:
            self.validate_audit.validate_source_command_shapes(catalog)
        self.assertIn("baseline", str(ctx.exception))

    def test_us07_source_requires_repeated_inspections(self) -> None:
        catalog = json.loads((TOOL / "us88_catalog.json").read_text(encoding="utf-8"))
        us07 = next(row for row in catalog["entries"] if row["id"] == "US-07")
        cmds = us07["required_command_templates"]
        first_stop = next(
            i for i, cmd in enumerate(cmds) if cmd[:3] == ["vehicles", "automation", "stop"]
        )
        pre = cmds[:first_stop]
        self.assertGreaterEqual(
            sum(1 for cmd in pre if cmd[:3] == ["vehicles", "automation", "status"]),
            2,
        )
        self.assertGreaterEqual(
            sum(1 for cmd in pre if cmd[:3] == ["vehicles", "stream", "memory"]),
            2,
        )
        # Keep run + first status + first stream + stop + post-stop status only.
        us07["required_command_templates"] = [
            cmds[0],
            cmds[1],
            cmds[2],
            cmds[first_stop],
            *cmds[first_stop + 1 :],
        ]
        with self.assertRaises(self.validate_audit.AuditError) as ctx:
            self.validate_audit.validate_source_command_shapes(catalog)
        self.assertIn("US-07", str(ctx.exception))

    def test_boilerplate_output_and_prereq_rejected(self) -> None:
        inventory = json.loads((TOOL / "leaf_inventory.json").read_text(encoding="utf-8"))
        overlay = json.loads((TOOL / "leaf_overlay.json").read_text(encoding="utf-8"))
        leaf_ids = [row["leaf_id"] for row in inventory["leaves"]]
        leaf_id = "vehicles.memory.replay"
        overlay["leaves"][leaf_id]["output_contract"] = (
            "Human summary and optional machine payload for this leaf."
        )
        with self.assertRaises(self.validate_audit.AuditError) as ctx:
            self.validate_audit.validate_overlay(leaf_ids=leaf_ids, overlay=overlay)
        self.assertIn("boilerplate", str(ctx.exception))
        overlay["leaves"][leaf_id]["output_contract"] = (
            "Deterministic: yes (two independent passes matched)."
        )
        overlay["leaves"][leaf_id]["prerequisites"] = (
            "Repository checkout; Metrics UI when live"
        )
        with self.assertRaises(self.validate_audit.AuditError) as ctx:
            self.validate_audit.validate_overlay(leaf_ids=leaf_ids, overlay=overlay)
        self.assertIn("boilerplate", str(ctx.exception))

    def test_qualify_is_offline_deterministic(self) -> None:
        overlay = json.loads((TOOL / "leaf_overlay.json").read_text(encoding="utf-8"))
        row = overlay["leaves"]["vehicles.perception.qualify"]
        self.assertEqual(row["safety_class"], "local_write")
        self.assertEqual(row["validation_class"], "deterministic")
        self.assertEqual(row["owning_boundary"], "cli/automa_cli/physical_qualify.py")
        self.assertIsInstance(row["prerequisites"], str)
        self.assertIn("from-check-run", row["prerequisites"])
        self.assertNotIn("Metrics UI", row["prerequisites"])

    def test_replay_prereq_is_sequence_not_metrics_ui(self) -> None:
        overlay = json.loads((TOOL / "leaf_overlay.json").read_text(encoding="utf-8"))
        row = overlay["leaves"]["vehicles.memory.replay"]
        self.assertEqual(row["validation_class"], "deterministic")
        self.assertIn("sequence", row["prerequisites"].lower())
        self.assertNotIn("Metrics UI", row["prerequisites"])

    def test_registry_root_digest_must_match_catalog(self) -> None:
        catalog = self.validate_audit.load_catalog()
        cat_sha = self.validate_audit.catalog_digest()
        sequences = json.loads(
            (TOOL / "sequence_registry.json").read_text(encoding="utf-8")
        )
        sequences["catalog_digest"] = "0" * 64
        with self.assertRaises(self.validate_audit.AuditError) as ctx:
            self.validate_audit.validate_sequences(
                catalog=catalog,
                catalog_sha=cat_sha,
                sequences=sequences,
                leaf_ids=set(self.parser_walk.public_leaf_ids()),
            )
        self.assertIn("sequence_registry.catalog_digest", str(ctx.exception))

    def test_us07_prereq_requires_observer_and_interval(self) -> None:
        sequences = json.loads(
            (TOOL / "sequence_registry.json").read_text(encoding="utf-8")
        )
        for row in sequences["sequences"]:
            if row["id"] == "US-07":
                row["prerequisites"] = "Operator-chosen observation interval only"
        with self.assertRaises(self.validate_audit.AuditError) as ctx:
            self.validate_audit.validate_sequence_source_prerequisites(sequences)
        self.assertIn("observer", str(ctx.exception).lower())

    def test_help_unknown_option_before_help_fails(self) -> None:
        receipt = self.argv_validate.validate_argv(
            ["vehicles", "automation", "run", "--bogus", "--help"],
            template_id="help-unknown-opt",
        )
        self.assertFalse(receipt.ok)
        self.assertIn("invalid tokens before help", receipt.reason)

    def test_help_extra_positional_before_help_fails(self) -> None:
        receipt = self.argv_validate.validate_argv(
            ["vehicles", "automation", "run", "bogus", "--help"],
            template_id="help-extra-pos",
        )
        self.assertFalse(receipt.ok)
        self.assertIn("invalid tokens before help", receipt.reason)

    def test_help_invalid_choice_before_help_fails(self) -> None:
        # --candidate is free-form; --control-algorithm is a closed argparse choice.
        receipt = self.argv_validate.validate_argv(
            [
                "vehicles",
                "perception",
                "qualify",
                "--control-algorithm",
                "bogus",
                "--help",
            ],
            template_id="help-bad-choice",
        )
        self.assertFalse(receipt.ok)
        self.assertIn("invalid tokens before help", receipt.reason)

    def test_help_prefix_uses_supplied_parser(self) -> None:
        import argparse

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd", required=True)
        foo = sub.add_parser("foo")
        foo.add_argument("path")
        foo.add_argument("--mode", choices=["alpha", "beta"])
        ok = self.argv_validate.validate_argv(
            ["foo", "x", "--help"],
            parser=parser,
            template_id="alt-help",
        )
        self.assertTrue(ok.ok, ok.reason)
        self.assertEqual(ok.reason, "ok_help")
        without_help = self.argv_validate.validate_argv(
            ["foo", "x"],
            parser=parser,
            template_id="alt-plain",
        )
        self.assertTrue(without_help.ok, without_help.reason)
        bad = self.argv_validate.validate_argv(
            ["foo", "x", "--mode", "nope", "--help"],
            parser=parser,
            template_id="alt-bad-choice",
        )
        self.assertFalse(bad.ok)
        self.assertIn("invalid tokens before help", bad.reason)

    def test_help_valid_positional_prefix_ok(self) -> None:
        receipt = self.argv_validate.validate_argv(
            [
                "vehicles",
                "perception",
                "apply",
                "/tmp/x",
                "--algorithm",
                "lightweight_observer",
                "--help",
            ],
            template_id="help-valid-pos",
        )
        self.assertTrue(receipt.ok, receipt.reason)

    def test_us01_help_predicates_fail_when_help_steps_removed(self) -> None:
        result_path = (
            ROOT
            / "docs/milestones/007-cli-operator-usability/evidence/"
            "live-cli-acceptance/result.json"
        )
        parsed = json.loads(result_path.read_text(encoding="utf-8"))
        parsed["ordered_command_outcomes"] = [
            row
            for row in parsed["ordered_command_outcomes"]
            if not str(row.get("step_id") or "").startswith("help-")
            and " --help" not in str(row.get("command") or "")
        ]
        claim_map = json.loads((TOOL / "claim_map.json").read_text(encoding="utf-8"))
        claim = claim_map["claims"]["us01_help_discovery"]
        with self.assertRaises(self.validate_audit.AuditError) as ctx:
            self.validate_audit.eval_cite_predicates("US-01", claim, parsed)
        self.assertIn("help", str(ctx.exception).lower())

    def test_live_residual_fabricated_identity_fails(self) -> None:
        sequences = json.loads(
            (TOOL / "sequence_registry.json").read_text(encoding="utf-8")
        )
        overlay = json.loads((TOOL / "leaf_overlay.json").read_text(encoding="utf-8"))
        residuals = json.loads((TOOL / "live_residuals.json").read_text(encoding="utf-8"))
        for row in residuals["findings"]:
            row["owner"] = "fabricated-owner"
            row["ledger_owner"] = "fabricated ledger owner"
            row["links"] = {"leaves": ["vehicles.status"], "sequences": ["US-01"]}
        with self.assertRaises(self.validate_audit.AuditError) as ctx:
            self.validate_audit.validate_live_residuals(
                sequences=sequences,
                overlay=overlay,
                residuals=residuals["findings"],
            )
        msg = str(ctx.exception).lower()
        self.assertTrue("ledger" in msg or "identity" in msg or "links" in msg, msg)

    def test_cite_digest_frozen_against_registry_resign(self) -> None:
        result_path = (
            ROOT
            / "docs/milestones/007-cli-operator-usability/evidence/"
            "live-cli-acceptance/result.json"
        )
        original = result_path.read_text(encoding="utf-8")
        sequences = json.loads(
            (TOOL / "sequence_registry.json").read_text(encoding="utf-8")
        )
        claim_map = json.loads((TOOL / "claim_map.json").read_text(encoding="utf-8"))
        try:
            mutated = original.replace("./cli/automa help", "./cli/automa help MUTATED", 1)
            self.assertNotEqual(mutated, original)
            result_path.write_text(mutated, encoding="utf-8")
            import hashlib

            digest = hashlib.sha256(mutated.encode("utf-8")).hexdigest()
            rel = (
                "docs/milestones/007-cli-operator-usability/evidence/"
                "live-cli-acceptance/result.json"
            )
            for row in sequences["sequences"]:
                evidence = row.get("evidence") or {}
                if rel in (evidence.get("digests") or {}):
                    evidence["digests"][rel] = digest
            with self.assertRaises(self.validate_audit.AuditError) as ctx:
                self.validate_audit.validate_semantic_cite(
                    sequences=sequences,
                    claim_map=claim_map,
                    repo_root=ROOT,
                )
            self.assertIn("frozen", str(ctx.exception).lower())
        finally:
            result_path.write_text(original, encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
