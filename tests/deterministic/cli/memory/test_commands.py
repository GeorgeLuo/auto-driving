from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from tests.support.runtime_fixtures import write_host_fixture

from autonomy.decision_cycle.activation import read_step_activation
from cli.automa_cli.hosts.workbench_runner import ImageReplayRunner
from implementations.decision_cycle.catalog import PRESET_STEPS
from implementations.decision_cycle.memory.presets import DEFAULT_MEMORY_PRESET
from tests.shared.cli.invoke import invoke_automa


class MemoryCommandTests(unittest.TestCase):
    def test_memory_update_and_info_use_catalog_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            write_host_fixture(runtime_root, "chase-sim-chaser")
            update = invoke_automa(
                "hosts", "update", "--name", "chase-sim-chaser", "--json",
                runtime_root=runtime_root,
            )
            info = invoke_automa(
                "hosts", "info",
                "memory",
                "--name",
                "chase-sim-chaser",
                "--json",
                runtime_root=runtime_root,
            )

            update_payload = json.loads(update.stdout)
            self.assertEqual(update_payload["presets"]["memory"], DEFAULT_MEMORY_PRESET)
            self.assertEqual(update_payload["steps"]["memory"], ["bounded_evidence"])
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
            self.assertEqual(info_payload["schema"], "host_memory_info")
            self.assertNotIn("lifecycle", info_payload)
            self.assertEqual(info_payload["memory_schema"]["schema"], "memory_schema_v1")
            self.assertEqual(
                set(info_payload["memory_schema"]["failure_policy"]),
                {"update", "reset", "missing_input"},
            )
            self.assertEqual(info_payload["activation"]["plugins"], ["bounded_evidence"])
            self.assertEqual(
                info_payload["activation"]["plugin_specs"]["bounded_evidence"],
                "implementations.decision_cycle.memory.plugins.bounded_evidence.plugin:BoundedEvidenceLedger",
            )
            # A step holds a list of plugins; none of them stands for the step.
            self.assertNotIn("plugin_id", info_payload["activation"])
            # Retention bounds belong to the plugin, not the activation.
            self.assertNotIn("bounds", info_payload["activation"])

    def test_info_lists_staged_plugins_without_loading_unselected_ones(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            write_host_fixture(runtime_root, "chase-sim-chaser")
            invoke_automa("hosts", "update", "--name", "chase-sim-chaser", runtime_root=runtime_root)
            path = runtime_root / "chase-sim-chaser/bundle/runtime/memory/active.json"
            activation = json.loads(path.read_text())
            activation["plugin_specs"]["missing"] = "implementations.memory.not_installed:Plugin"
            path.write_text(json.dumps(activation))
            info = invoke_automa(
                "hosts", "info", "memory", "--name", "chase-sim-chaser", "--json",
                runtime_root=runtime_root,
            )
            expected = ["bounded_evidence", "missing"]
            self.assertEqual(sorted(json.loads(info.stdout)["activation"]["plugin_specs"]), expected)

    def test_info_names_the_staged_preset_and_plugins_as_perception_info_does(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            write_host_fixture(runtime_root, "chase-sim-chaser")
            invoke_automa("hosts", "update", "--name", "chase-sim-chaser", runtime_root=runtime_root)
            staged = {}
            presets = {}
            for step in PRESET_STEPS:
                info = ("hosts", "info", step, "--name", "chase-sim-chaser")
                payload = json.loads(
                    invoke_automa(*info, "--json", runtime_root=runtime_root).stdout
                )
                text = invoke_automa(*info, runtime_root=runtime_root).stdout.splitlines()
                activation = payload["activation"]
                preset = activation["preset"]
                self.assertEqual(
                    text[0], f"{step.title()}: chase-sim-chaser -> {preset}"
                )
                self.assertEqual(
                    text[1], f"Enabled plugins: {', '.join(activation['plugins'])}"
                )
                # The packaged catalog is listed by `plugins list`, not by info.
                self.assertNotIn("available_plugins", activation)
                self.assertFalse(any(line.startswith("Available plugins") for line in text))
                # Every selectable step declares a schema and reports the same
                # section titles and failure-policy field names.
                schema = payload[f"{step}_schema"]
                self.assertEqual(set(schema["failure_policy"]), {"update", "reset", "missing_input"})
                self.assertIn("order", schema["composition"])
                self.assertIn("Composition:", text)
                self.assertIn("Failure policy:", text)
                self.assertTrue(any(line.startswith("Schema source:") for line in text))
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

    def test_memory_info_missing_activation_is_actionable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            write_host_fixture(runtime_root, "chase-sim-chaser")
            result = invoke_automa(
                "hosts", "info",
                "memory",
                "--name",
                "chase-sim-chaser",
                runtime_root=runtime_root,
                check=False,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No active memory activation found", result.stdout)
        self.assertIn("hosts update --name chase-sim-chaser", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
