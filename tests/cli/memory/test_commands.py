from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests.support.cli_runner import run_automa
from tests.cli.memory.replay_fixtures import RECURRENCE_SOURCE


class MemoryCommandTests(unittest.TestCase):
    def test_memory_update_and_info_use_catalog_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            update = run_automa(
                "vehicles",
                "update",
                "memory",
                "--id",
                "chase-sim-chaser",
                "--plugin",
                "bounded_evidence",
                "--json",
                runtime_root=runtime_root,
            )
            info = run_automa(
                "vehicles",
                "info",
                "memory",
                "--id",
                "chase-sim-chaser",
                "--json",
                runtime_root=runtime_root,
            )

            update_payload = json.loads(update.stdout)
            self.assertEqual(update_payload["schema"], "vehicle_memory_update_v1")
            self.assertEqual(update_payload["plugins"], ["bounded_evidence"])
            self.assertEqual(update_payload["manifest"]["step"], "memory")
            self.assertEqual(
                update_payload["manifest"]["plugin_specs"]["bounded_evidence"],
                "implementations.decision_cycle.memory.bounded_evidence.plugin:BoundedEvidenceLedger",
            )
            self.assertIsNotNone(update_payload["release"]["tree_sha256"])

            activation_path = (
                runtime_root
                / "chase-sim-chaser"
                / "bundle"
                / "runtime"
                / "memory"
                / "active.json"
            )
            self.assertTrue(activation_path.is_file())

            info_payload = json.loads(info.stdout)
            self.assertEqual(info_payload["schema"], "vehicle_memory_info_v0")
            self.assertEqual(info_payload["activation"]["plugin_id"], "bounded_evidence")
            self.assertEqual(
                info_payload["activation"]["implementation_id"],
                "bounded_evidence",
            )
            # Retention bounds belong to the plugin, not the activation.
            self.assertNotIn("bounds", info_payload["activation"])
            self.assertFalse(info_payload["lifecycle"]["claims_identity"])

    def test_memory_enable_disable_commands_round_trip_through_info(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            run_automa("vehicles", "update", "memory", "--id", "test-car", runtime_root=runtime_root)
            for command, selected, changed in (
                ("disable", [], True),
                ("disable", [], False),
                ("enable", ["bounded_evidence"], True),
                ("enable", ["bounded_evidence"], False),
            ):
                result = run_automa(
                    "vehicles", "memory", command, "--id", "test-car", "bounded_evidence", "--json",
                    runtime_root=runtime_root,
                )
                payload = json.loads(result.stdout)
                self.assertEqual(payload["plugins_after"], selected)
                self.assertEqual(payload["changed"], changed)
                info = run_automa(
                    "vehicles", "info", "memory", "--id", "test-car", "--json",
                    runtime_root=runtime_root,
                )
                activation = json.loads(info.stdout)["activation"]
                self.assertEqual(activation["plugins"], selected)
                self.assertEqual(
                    activation["available_plugins"], ["bounded_evidence", "multi_obstruction_tracks"]
                )
                self.assertEqual(activation["plugin_id"], selected[-1] if selected else None)
                self.assertEqual(
                    activation["implementation_id"],
                    "bounded_evidence" if selected else None,
                )
                if command == "disable" and changed:
                    replay = run_automa(
                        "vehicles", "memory", "replay", str(RECURRENCE_SOURCE),
                        "--id", "test-car", "--json", runtime_root=runtime_root,
                    )
                    result = json.loads(replay.stdout)
                    self.assertEqual(result["plugin_ids"], [])
                    self.assertEqual(result["final"], {})
                    self.assertTrue(result["deterministic"])

    def test_info_and_selection_share_staged_catalog_without_loading_unselected_plugins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            run_automa("vehicles", "update", "memory", "--id", "test-car", runtime_root=runtime_root)
            path = runtime_root / "test-car/bundle/runtime/memory/active.json"
            activation = json.loads(path.read_text())
            activation["plugin_specs"]["missing"] = "implementations.memory.not_installed:Plugin"
            path.write_text(json.dumps(activation))
            info = run_automa(
                "vehicles", "info", "memory", "--id", "test-car", "--json",
                runtime_root=runtime_root,
            )
            expected = ["bounded_evidence", "missing", "multi_obstruction_tracks"]
            self.assertEqual(json.loads(info.stdout)["activation"]["available_plugins"], expected)
            disabled = run_automa(
                "vehicles", "memory", "disable", "--id", "test-car", "missing", "--json",
                runtime_root=runtime_root,
            )
            self.assertEqual(json.loads(disabled.stdout)["available_plugins"], expected)
            self.assertFalse(json.loads(disabled.stdout)["changed"])
            saved = path.read_text()
            rejected = run_automa(
                "vehicles", "memory", "enable", "--id", "test-car", "missing",
                runtime_root=runtime_root, check=False,
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertEqual(path.read_text(), saved)

    def test_memory_update_dry_run_does_not_write_activation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            result = run_automa(
                "vehicles",
                "update",
                "memory",
                "--id",
                "chase-sim-chaser",
                "--dry-run",
                "--json",
                runtime_root=runtime_root,
            )

            payload = json.loads(result.stdout)
            activation = (
                runtime_root
                / "chase-sim-chaser"
                / "bundle"
                / "runtime"
                / "memory"
                / "active.json"
            )
            self.assertTrue(payload["dry_run"])
            self.assertFalse(activation.exists())

    def test_memory_info_missing_activation_is_actionable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            result = run_automa(
                "vehicles",
                "info",
                "memory",
                "--id",
                "chase-sim-chaser",
                runtime_root=runtime_root,
                check=False,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No active memory implementation", result.stdout)
        self.assertIn("vehicles update memory", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
