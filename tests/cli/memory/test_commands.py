from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from autonomy.decision_cycle.activation import read_step_activation
from cli.automa_cli.workbench_runner import ImageReplayRunner
from implementations.decision_cycle.catalog import preset_activation
from implementations.decision_cycle.memory.presets import (
    DEFAULT_MEMORY_PRESET,
    MEMORY_PRESETS,
)
from tests.support.cli_runner import run_automa


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

    def test_info_lists_staged_plugins_without_loading_unselected_ones(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            run_automa("vehicles", "update", "memory", "--id", "chase-sim-chaser", runtime_root=runtime_root)
            path = runtime_root / "chase-sim-chaser/bundle/runtime/memory/active.json"
            activation = json.loads(path.read_text())
            activation["plugin_specs"]["missing"] = "implementations.memory.not_installed:Plugin"
            path.write_text(json.dumps(activation))
            info = run_automa(
                "vehicles", "info", "memory", "--id", "chase-sim-chaser", "--json",
                runtime_root=runtime_root,
            )
            expected = ["bounded_evidence", "missing"]
            self.assertEqual(json.loads(info.stdout)["activation"]["available_plugins"], expected)

    def test_info_names_the_staged_preset_as_perception_info_does(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            staged = {}
            presets = {}
            for step in ("perception", "memory"):
                updated = run_automa(
                    "vehicles",
                    "update",
                    step,
                    "--id",
                    "chase-sim-chaser",
                    runtime_root=runtime_root,
                    check=False,
                )
                self.assertIn(f"Updated {step}: chase-sim-chaser", updated.stdout)
                info = ("vehicles", "info", step, "--id", "chase-sim-chaser")
                payload = json.loads(
                    run_automa(*info, "--json", runtime_root=runtime_root).stdout
                )
                text = run_automa(*info, runtime_root=runtime_root).stdout.splitlines()
                activation = payload["activation"]
                preset = activation["preset"]
                self.assertEqual(
                    text[0], f"{step.title()}: chase-sim-chaser -> {preset}"
                )
                self.assertEqual(
                    text[1], f"Enabled plugins: {', '.join(activation['plugins'])}"
                )
                staged[step] = read_step_activation(activation["path"], step)
                presets[step] = preset

            self.assertEqual(presets["memory"], DEFAULT_MEMORY_PRESET)
            runner = ImageReplayRunner(activations=staged)
            try:
                pipeline = runner.state()["machine_detail"]["pipeline"]
                for step, preset in presets.items():
                    self.assertEqual(pipeline[f"{step}_preset"], preset)
            finally:
                runner.close()

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
                        "vehicles", "update", "memory", "--id", "chase-sim-chaser",
                        "--preset", preset, "--dry-run", "--json",
                        runtime_root=runtime_root,
                    ).stdout)
                    expected = preset_activation("memory", preset)
                    self.assertEqual(dry["preset"], preset)
                    self.assertEqual(dry["plugins"], list(expected.plugins))
                    self.assertEqual(dry["manifest"], expected.to_payload())

                    run_automa(
                        "vehicles", "update", "memory", "--id", "chase-sim-chaser",
                        "--preset", preset, "--json", runtime_root=runtime_root,
                    )
                    staged = json.loads(
                        (runtime_root / "chase-sim-chaser/bundle/runtime/memory/active.json").read_text()
                    )
                    self.assertEqual(staged["plugins"], list(expected.plugins))
                    self.assertEqual(staged["metadata"]["preset"], preset)

    def test_update_memory_without_a_selection_stages_the_default_preset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            payload = json.loads(run_automa(
                "vehicles", "update", "memory", "--id", "chase-sim-chaser", "--dry-run", "--json",
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
                (["bounded_evidence"], "recency_ledger"),
            ):
                with self.subTest(plugins=plugins):
                    options = [option for plugin in plugins for option in ("--plugin", plugin)]
                    payload = json.loads(run_automa(
                        "vehicles", "update", "memory", "--id", "chase-sim-chaser", *options,
                        "--dry-run", "--json", runtime_root=runtime_root,
                    ).stdout)
                    self.assertEqual(payload["preset"], label)
                    self.assertEqual(payload["manifest"]["metadata"]["preset"], label)

    def test_update_memory_rejects_a_preset_with_plugins_and_an_unknown_preset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            both = run_automa(
                "vehicles", "update", "memory", "--id", "chase-sim-chaser",
                "--preset", "recency_ledger", "--plugin", "bounded_evidence",
                runtime_root=runtime_root, check=False,
            )
            self.assertEqual(both.returncode, 2)
            self.assertIn("not allowed with argument", both.stderr)
            unknown = run_automa(
                "vehicles", "update", "memory", "--id", "chase-sim-chaser", "--preset", "nope",
                runtime_root=runtime_root, check=False,
            )
            self.assertEqual(unknown.returncode, 2)
            self.assertIn("invalid choice: 'nope'", unknown.stderr)
            for preset in MEMORY_PRESETS:
                self.assertIn(preset, unknown.stderr)
            self.assertFalse((runtime_root / "chase-sim-chaser").exists())

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
        self.assertIn("No active memory preset found", result.stdout)
        self.assertIn("vehicles update memory", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
