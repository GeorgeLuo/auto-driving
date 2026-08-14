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
        # Point US-04 at incomplete machine-only package.
        bad_path = (
            "docs/milestones/007-cli-operator-usability/evidence/"
            "cli-scenario-continuity/machine-only-session/result.json"
        )
        for row in sequences["sequences"]:
            if row["id"] == "US-04":
                row["evidence"] = {
                    "evidence_mode": "cited",
                    "claim_map_id": "continuity_live_config_swap",
                    "digests": {},
                    "source_pr": 100,
                    "head_claim": "historical",
                }
        claim_map["claims"]["continuity_live_config_swap"]["paths"] = [bad_path]
        # Incomplete result should fail predicate result==pass
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


if __name__ == "__main__":
    unittest.main()
