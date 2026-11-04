from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from tests.support.runtime_fixtures import write_host_fixture

from autonomy.decision_cycle.activation import read_step_activation
from cli.automa_cli.workbench.runner import ImageReplayRunner
from implementations.decision_cycle.catalog import DEFAULT_STEP_PRESETS, PRESET_STEPS
from tests.shared.cli.invoke import invoke_automa


class MemoryCommandTests(unittest.TestCase):
    def test_memory_update_stages_the_catalog_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            write_host_fixture(runtime_root, "chase-sim-chaser")
            update = invoke_automa(
                "hosts", "update", "--name", "chase-sim-chaser", "--json",
                runtime_root=runtime_root,
            )

            update_payload = json.loads(update.stdout)
            self.assertEqual(update_payload["presets"]["memory"], DEFAULT_STEP_PRESETS["memory"])
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
            activation = json.loads(activation_path.read_text(encoding="utf-8"))
            self.assertEqual(activation["plugins"], ["bounded_evidence"])
            self.assertEqual(
                activation["plugin_specs"]["bounded_evidence"],
                "implementations.decision_cycle.memory.plugins.bounded_evidence.plugin:BoundedEvidenceLedger",
            )
            # A step holds a list of plugins; none of them stands for the step.
            self.assertNotIn("plugin_id", activation)
            # Retention bounds belong to the plugin, not the activation.
            self.assertNotIn("bounds", activation)

    def test_status_names_each_staged_preset_as_the_workbench_runs_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            write_host_fixture(runtime_root, "chase-sim-chaser")
            invoke_automa("hosts", "update", "--name", "chase-sim-chaser", runtime_root=runtime_root)
            run = json.loads(invoke_automa(
                "hosts", "status", "--json", runtime_root=runtime_root,
            ).stdout)["hosts"][0]["run"]
            human = invoke_automa("hosts", "status", "--long", runtime_root=runtime_root).stdout
            staged = {}
            presets = {}
            for step in PRESET_STEPS:
                selection = run["steps"][step]
                preset = selection["preset"]
                self.assertEqual((selection["source"], selection["saved"]), ("staged", False))
                self.assertIn(
                    f"\n    {step}: {preset} ({', '.join(selection['plugins'])})  source=staged (default)\n",
                    human,
                )
                staged[step] = read_step_activation(Path(selection["activation"]), step)
                presets[step] = preset

            self.assertEqual(presets["memory"], DEFAULT_STEP_PRESETS["memory"])
            runner = ImageReplayRunner(activations=staged)
            try:
                pipeline = runner.state()["machine_detail"]["pipeline"]
                for step, preset in presets.items():
                    self.assertEqual(pipeline[f"{step}_preset"], preset)
            finally:
                runner.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
