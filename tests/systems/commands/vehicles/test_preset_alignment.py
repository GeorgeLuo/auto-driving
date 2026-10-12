"""Perception, memory and proposal obey the same preset-selection behaviors."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from cli.automa_cli.workbench_runner import ImageReplayRunner
from implementations.decision_cycle.catalog import DEFAULT_STEP_PRESETS, selection_activation
from tests.shared.cli.workbench_fixtures import image_source, write_manifest
from tests.support.cli_runner import run_automa
from tests.support.fake_metrics_ui import fake_metrics_ui_server

PRESET_STEPS = ("perception", "memory", "proposal")


class PresetAlignmentTests(unittest.TestCase):
    def test_default_and_named_preset_stage_equivalent_selections_across_steps(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            for step in PRESET_STEPS:
                with self.subTest(step=step):
                    command = ("vehicles", "update", step, "--id", "chase-sim-chaser", "--dry-run", "--json")
                    implicit = json.loads(run_automa(*command, runtime_root=runtime_root).stdout)
                    explicit = json.loads(run_automa(*command, "--preset", DEFAULT_STEP_PRESETS[step],
                                                     runtime_root=runtime_root).stdout)
                    for field in ("plugins", "plugin_configs", "plugin_specs"):
                        self.assertEqual(implicit["manifest"][field], explicit["manifest"][field])
                    self.assertEqual(implicit["manifest"]["metadata"]["preset"], explicit["preset"])
                    self.assertFalse(runtime_root.exists())

    def test_plugin_selection_preserves_tuning_and_preset_selection_restores_it_across_steps(self):
        # Settings differ by step; selection and configuration precedence do not.
        cases = (
            ("perception", "multi_obstruction", "multi_obstruction_tracks", "max_tracks", 1),
            ("memory", "recency_ledger", "bounded_evidence", "max_records", 1),
            ("proposal", "obstruction_avoidance", "avoid_recent_obstruction", "steer_magnitude", 0.25),
        )
        with tempfile.TemporaryDirectory() as tmp, fake_metrics_ui_server() as ws_url:
            runtime_root = Path(tmp) / "vehicles"
            env = {"CHASE_UI_WS_URL": ws_url}
            # Perception also checks passive simulator readiness after staging.
            for prerequisite in ("memory", "proposal"):
                run_automa("vehicles", "update", prerequisite, "--id", "chase-sim-chaser",
                           runtime_root=runtime_root, extra_env=env)
            for step, preset, plugin_id, setting, value in cases:
                with self.subTest(step=step):
                    def update(*flags):
                        return json.loads(run_automa("vehicles", "update", step, "--id", "chase-sim-chaser",
                                                     *flags, "--json", runtime_root=runtime_root, extra_env=env).stdout)

                    recipe = update("--preset", preset)
                    path = runtime_root / f"chase-sim-chaser/bundle/runtime/{step}/active.json"
                    tuned = json.loads(path.read_text())
                    self.assertIn(plugin_id, tuned["plugins"])
                    self.assertNotEqual(tuned["plugin_configs"][plugin_id][setting], value)
                    tuned["plugin_configs"][plugin_id][setting] = value
                    path.write_text(json.dumps(tuned))
                    flags = tuple(arg for plugin in tuned["plugins"] for arg in ("--plugin", plugin))
                    selected = update(*flags)
                    self.assertEqual(selected["manifest"]["plugin_configs"], tuned["plugin_configs"])
                    restored = update("--preset", preset)
                    self.assertEqual(restored["manifest"]["plugin_configs"], recipe["manifest"]["plugin_configs"])
                    info = json.loads(run_automa("vehicles", "info", step, "--id", "chase-sim-chaser",
                                                 "--json", runtime_root=runtime_root).stdout)
                    self.assertEqual(info["activation"]["preset"], restored["preset"])
                    self.assertEqual(info["activation"]["plugins"], restored["manifest"]["plugins"])

    def test_inspect_restoration_and_preset_override_behave_the_same_across_steps(self):
        with image_source(1) as root:
            original = json.loads(run_automa("vehicles", "proposal", "inspect", str(root), "--json").stdout)
            for step in PRESET_STEPS:
                with self.subTest(step=step):
                    recorded = copy.deepcopy(original)
                    for frame in recorded["frames"]:
                        frame["steps"][step]["plugins"] = []
                        frame["steps"][step]["metadata"]["preset"] = "custom"
                    write_manifest(root, recorded)
                    command = ("vehicles", step, "inspect", str(root), "--json")
                    restored = json.loads(run_automa(*command).stdout)
                    self.assertEqual(restored["frames"][0]["steps"][step]["plugins"], [])
                    overridden = json.loads(run_automa(*command, "--preset", DEFAULT_STEP_PRESETS[step]).stdout)
                    fresh = selection_activation(step).to_payload()
                    for field in ("plugins", "plugin_specs", "plugin_configs"):
                        self.assertEqual(overridden["frames"][0]["steps"][step][field], fresh[field])
                    self.assertEqual(overridden["frames"][0]["steps"][step]["metadata"]["preset"],
                                     DEFAULT_STEP_PRESETS[step])

    def test_cli_and_workbench_apply_equivalent_default_selections_across_steps(self):
        with image_source(1) as root, tempfile.TemporaryDirectory() as tmp:
            for step in PRESET_STEPS:
                with self.subTest(step=step):
                    staged = json.loads(run_automa("vehicles", "update", step, "--id", "chase-sim-chaser",
                                                  "--dry-run", "--json", runtime_root=Path(tmp)).stdout)
                    for activations in (None, {step: selection_activation(step, preset=DEFAULT_STEP_PRESETS[step])}):
                        runner = ImageReplayRunner(root, activations=activations, cadence_ms=0, loop=False)
                        runner.start()
                        state = runner.wait(10)
                        self.assertEqual(state["phase"], "completed")
                        self.assertEqual(state[f"active_{step}_plugin_ids"], staged["manifest"]["plugins"])
                        self.assertEqual(state["machine_detail"]["pipeline"][f"{step}_preset"], staged["preset"])
                        self.assertEqual(state["machine_detail"]["pipeline"][f"{step}_plugin_report"]["applied_plugin_ids"],
                                         staged["manifest"]["plugins"])


if __name__ == "__main__":
    unittest.main()
