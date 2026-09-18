from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests.support.cli_runner import run_automa


class DecisionCommandTests(unittest.TestCase):
    def test_decision_update_and_info_use_engine_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            update = run_automa(
                "vehicles",
                "update",
                "decision",
                "--id",
                "chase-sim-chaser",
                "--engine",
                "idle",
                "--json",
                runtime_root=runtime_root,
            )
            info = run_automa(
                "vehicles",
                "info",
                "decision",
                "--id",
                "chase-sim-chaser",
                "--json",
                runtime_root=runtime_root,
            )

        update_payload = json.loads(update.stdout)
        self.assertEqual(update_payload["schema"], "vehicle_decision_update_v0")
        self.assertEqual(update_payload["manifest"]["decision"]["engine_id"], "idle")
        self.assertIsNotNone(update_payload["release"]["tree_sha256"])

        info_payload = json.loads(info.stdout)
        self.assertEqual(info_payload["schema"], "vehicle_decision_info_v0")
        self.assertEqual(info_payload["activation"]["engine_id"], "idle")
        self.assertEqual(info_payload["engine_schema"]["schema"], "autonomy_engine_schema_v0")
        self.assertEqual(info_payload["engine_schema_source"]["method"], "describe_schema")

    def test_decision_update_dry_run_does_not_write_activation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            result = run_automa(
                "vehicles",
                "update",
                "decision",
                "--id",
                "chase-sim-chaser",
                "--dry-run",
                "--json",
                runtime_root=runtime_root,
            )

            payload = json.loads(result.stdout)
            activation = runtime_root / "chase-sim-chaser" / "bundle" / "runtime" / "decision" / "active.json"
            self.assertTrue(payload["dry_run"])
            self.assertFalse(activation.exists())

    def test_shadow_info_probe_is_read_only_without_a_runtime_producer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            update = run_automa(
                "vehicles",
                "update",
                "decision",
                "--id",
                "chase-sim-chaser",
                "--engine",
                "shadow-proposals",
                "--json",
                runtime_root=runtime_root,
            )
            self.assertEqual(update.returncode, 0, update.stderr + update.stdout)

            def snapshot_files() -> dict[Path, bytes]:
                return {
                    path.relative_to(runtime_root): path.read_bytes()
                    for path in runtime_root.rglob("*")
                    if path.is_file()
                }

            before_info = snapshot_files()
            info = run_automa(
                "vehicles",
                "info",
                "decision",
                "--id",
                "chase-sim-chaser",
                "--json",
                runtime_root=runtime_root,
            )
            self.assertEqual(info.returncode, 0, info.stderr + info.stdout)
            payload = json.loads(info.stdout)
            self.assertEqual(payload["schema"], "vehicle_decision_info_v0")
            self.assertEqual(payload["activation"]["engine_id"], "shadow-proposals")
            combined = payload["combined_view"]
            self.assertFalse(combined["available"])
            self.assertEqual(combined["status"], "unavailable")
            self.assertTrue(combined["reason"])
            self.assertIsNone(combined["url"])
            self.assertIsNone(combined["api_url"])
            self.assertEqual(before_info, snapshot_files())


if __name__ == "__main__":
    unittest.main(verbosity=2)
