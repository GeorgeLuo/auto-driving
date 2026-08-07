"""Deterministic tests for M007-10 continuity contract helpers."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import importlib.util

_CC_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs/milestones/007-cli-operator-usability/tools/live-cli-session-runner/continuity_contract.py"
)
_spec = importlib.util.spec_from_file_location("continuity_contract_under_test", _CC_PATH)
assert _spec and _spec.loader
cc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cc)


def _base_catalog(steps: list[dict]) -> dict:
    return {
        "schema": "live_cli_session_catalog_v0",
        "id": "m007-continuity-test",
        "track": "continuity",
        "steps": steps,
    }


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
                {
                    "id": "ok1",
                    "family_id": "continuity.live_config_swap",
                    "safety": "live_mutation",
                    "commands": [
                        [
                            "./cli/automa",
                            "vehicles",
                            "automation",
                            "run",
                            "--id",
                            "x",
                            "--observe-only",
                        ]
                    ],
                },
                {
                    "id": "ok2",
                    "family_id": "continuity.memory_lifecycle",
                    "safety": "live_mutation",
                    "commands": [
                        ["./cli/automa", "vehicles", "memory", "check", "--id", "x"]
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
                {
                    "id": "ok1",
                    "family_id": "continuity.live_config_swap",
                    "safety": "live_mutation",
                    "commands": [
                        [
                            "./cli/automa",
                            "vehicles",
                            "automation",
                            "stop",
                            "--id",
                            "x",
                        ]
                    ],
                },
                {
                    "id": "ok2",
                    "family_id": "continuity.memory_lifecycle",
                    "safety": "live_mutation",
                    "commands": [
                        ["./cli/automa", "vehicles", "memory", "reset", "--id", "x"]
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


class ContinuityFamilyTests(unittest.TestCase):
    def test_missing_required_family(self) -> None:
        catalog = _base_catalog(
            [
                {
                    "id": "a",
                    "family_id": "continuity.offline_perception",
                    "commands": [
                        ["./cli/automa", "vehicles", "perception", "run", "--id", "x"]
                    ],
                }
            ]
        )
        ok, reason, _ = cc.validate_continuity_families(catalog)
        self.assertFalse(ok)
        self.assertIn("missing required", reason)

    def test_help_only_required_family_rejected(self) -> None:
        catalog = _base_catalog(
            [
                {
                    "id": "a",
                    "family_id": "continuity.offline_perception",
                    "commands": [["./cli/automa", "help"]],
                },
                {
                    "id": "b",
                    "family_id": "continuity.live_config_swap",
                    "commands": [
                        [
                            "./cli/automa",
                            "vehicles",
                            "automation",
                            "run",
                            "--observe-only",
                            "--id",
                            "x",
                        ]
                    ],
                },
                {
                    "id": "c",
                    "family_id": "continuity.memory_lifecycle",
                    "commands": [
                        ["./cli/automa", "vehicles", "memory", "check", "--id", "x"]
                    ],
                },
            ]
        )
        ok, reason, _ = cc.validate_continuity_families(catalog)
        self.assertFalse(ok)
        self.assertIn("help/status-only", reason)

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

    def test_finalizer_refuses_stale_catalog_digest(self) -> None:
        recorded = {
            "catalog_sha256": "aaa",
            "runner_sha256": "bbb",
            "continuity_contract_sha256": "ccc",
            "product_sha256": {"cli/x.py": "ddd"},
            "metrics_ui": None,
        }
        current = dict(recorded)
        current["catalog_sha256"] = "zzz"
        ok, reason = cc.finalize_evidence_freshness(recorded, current)
        self.assertFalse(ok)
        self.assertIn("catalog_sha256", reason)

    def test_finalizer_ok_when_identical(self) -> None:
        bundle = {
            "catalog_sha256": "aaa",
            "runner_sha256": "bbb",
            "continuity_contract_sha256": "ccc",
            "product_sha256": {"cli/x.py": "ddd"},
            "metrics_ui": {"commit": "abc", "worktree_state": "clean"},
        }
        ok, reason = cc.finalize_evidence_freshness(bundle, bundle)
        self.assertTrue(ok, reason)


class RunIdCollisionTests(unittest.TestCase):
    def test_run_ids_unique_under_same_second(self) -> None:
        from cli.automa_cli.perception_runs import _run_id

        ids = {_run_id("apply", "same-source") for _ in range(20)}
        self.assertEqual(len(ids), 20)


if __name__ == "__main__":
    unittest.main()
