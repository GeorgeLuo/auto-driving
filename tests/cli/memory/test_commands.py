from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from implementations.decision_cycle.catalog import preset_activation
from implementations.decision_cycle.memory.presets import (
    DEFAULT_MEMORY_PRESET,
    MEMORY_PRESETS,
)
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
                "implementations.decision_cycle.memory.plugins.bounded_evidence.plugin:BoundedEvidenceLedger",
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
            self.assertEqual(info_payload["activation"]["plugins"], ["bounded_evidence"])
            # A step holds a list of plugins; none of them stands for the step.
            self.assertNotIn("plugin_id", info_payload["activation"])
            # Retention bounds belong to the plugin, not the activation.
            self.assertNotIn("bounds", info_payload["activation"])

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

    def test_update_memory_preset_stages_what_the_preset_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            for preset in MEMORY_PRESETS:
                with self.subTest(preset=preset):
                    dry = json.loads(run_automa(
                        "vehicles", "update", "memory", "--id", "test-car",
                        "--preset", preset, "--dry-run", "--json",
                        runtime_root=runtime_root,
                    ).stdout)
                    expected = preset_activation("memory", preset)
                    self.assertEqual(dry["preset"], preset)
                    self.assertEqual(dry["plugins"], list(expected.plugins))
                    self.assertEqual(dry["manifest"], expected.to_payload())

                    run_automa(
                        "vehicles", "update", "memory", "--id", "test-car",
                        "--preset", preset, "--json", runtime_root=runtime_root,
                    )
                    staged = json.loads(
                        (runtime_root / "test-car/bundle/runtime/memory/active.json").read_text()
                    )
                    self.assertEqual(staged["plugins"], list(expected.plugins))
                    self.assertEqual(staged["metadata"]["preset"], preset)

    def test_update_memory_without_a_selection_stages_the_default_preset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            payload = json.loads(run_automa(
                "vehicles", "update", "memory", "--id", "test-car", "--dry-run", "--json",
                runtime_root=runtime_root,
            ).stdout)
            self.assertEqual(payload["preset"], DEFAULT_MEMORY_PRESET)
            self.assertEqual(
                payload["plugins"], list(preset_activation("memory", DEFAULT_MEMORY_PRESET).plugins)
            )

    def test_update_memory_plugins_are_labeled_with_the_preset_they_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            for plugins, label in (
                (["multi_obstruction_tracks"], "multi_obstruction"),
                (["multi_obstruction_tracks", "bounded_evidence"], "custom"),
            ):
                with self.subTest(plugins=plugins):
                    options = [option for plugin in plugins for option in ("--plugin", plugin)]
                    payload = json.loads(run_automa(
                        "vehicles", "update", "memory", "--id", "test-car", *options,
                        "--dry-run", "--json", runtime_root=runtime_root,
                    ).stdout)
                    self.assertEqual(payload["preset"], label)
                    self.assertEqual(payload["manifest"]["metadata"]["preset"], label)

    def test_update_memory_rejects_a_preset_with_plugins_and_an_unknown_preset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            both = run_automa(
                "vehicles", "update", "memory", "--id", "test-car",
                "--preset", "recency_ledger", "--plugin", "bounded_evidence",
                runtime_root=runtime_root, check=False,
            )
            self.assertEqual(both.returncode, 2)
            self.assertIn("not allowed with argument", both.stderr)
            unknown = run_automa(
                "vehicles", "update", "memory", "--id", "test-car", "--preset", "nope",
                runtime_root=runtime_root, check=False,
            )
            self.assertEqual(unknown.returncode, 2)
            self.assertIn("invalid choice: 'nope'", unknown.stderr)
            for preset in MEMORY_PRESETS:
                self.assertIn(preset, unknown.stderr)
            self.assertFalse((runtime_root / "test-car").exists())

    def test_disabling_a_plugin_relabels_the_staged_preset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            path = runtime_root / "test-car/bundle/runtime/memory/active.json"
            run_automa(
                "vehicles", "update", "memory", "--id", "test-car",
                "--preset", "multi_obstruction_with_ledger", runtime_root=runtime_root,
            )
            self.assertEqual(json.loads(path.read_text())["metadata"]["preset"], "multi_obstruction_with_ledger")
            for command, plugin, label in (
                ("disable", "bounded_evidence", "multi_obstruction"),
                ("disable", "multi_obstruction_tracks", "custom"),
                ("enable", "multi_obstruction_tracks", "multi_obstruction"),
            ):
                with self.subTest(command=command, plugin=plugin):
                    run_automa(
                        "vehicles", "memory", command, "--id", "test-car", plugin,
                        runtime_root=runtime_root,
                    )
                    self.assertEqual(json.loads(path.read_text())["metadata"]["preset"], label)

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
