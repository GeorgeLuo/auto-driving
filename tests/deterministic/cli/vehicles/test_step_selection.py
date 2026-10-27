"""Perception, memory, proposal and plan select and stage through one update path."""

from __future__ import annotations

import contextlib
import copy
import json
import tempfile
import unittest
from collections.abc import Iterator
from pathlib import Path
from tests.support.runtime_fixtures import write_host_fixture
from unittest.mock import patch

from autonomy.decision_cycle.steps import CLEARABLE_STEPS
from cli.automa_cli.step_activations import CONTROLLER_BUNDLE_KEYS, bundle_activation_path, host_bundle
from cli.automa_cli.workbench_runner import ImageReplayRunner
from implementations.decision_cycle.catalog import (
    DEFAULT_STEP_PRESETS,
    SELECTABLE_STEPS,
    STEP_PRESETS,
    preset_activation,
    selection_activation,
)
from tests.shared.cli.invoke import invoke_automa
from tests.shared.cli.workbench_fixtures import image_source, write_manifest

VEHICLE = "chase-sim-chaser"
SELECTION_FIELDS = ("plugins", "plugin_specs", "plugin_configs")


def _invoke(*args: str) -> tuple[int, str, str]:
    result = invoke_automa(*args, check=False)
    return result.returncode, result.stdout, result.stderr


class StepSelectionTests(unittest.TestCase):
    @contextlib.contextmanager
    def _runtime(self) -> Iterator[Path]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_host_fixture(root, VEHICLE)
            with patch("cli.automa_cli.app.HOSTS_RUNTIME_ROOT", root):
                yield root

    def _update(self, step: str, *flags: str) -> dict:
        code, stdout, stderr = _invoke("hosts", "stage", step, "--name", VEHICLE, *flags, "--json")
        self.assertEqual(code, 0, stdout + stderr)
        return json.loads(stdout)

    def test_every_step_stages_its_default_preset_without_flags(self) -> None:
        """Regression: perception and memory each chose their default in their
        own update path, and proposal had no default preset to stage."""

        with self._runtime() as root:
            for step in SELECTABLE_STEPS:
                with self.subTest(step=step):
                    implicit = self._update(step, "--dry-run")
                    explicit = self._update(step, "--preset", DEFAULT_STEP_PRESETS[step], "--dry-run")
                    self.assertEqual(implicit["preset"], DEFAULT_STEP_PRESETS[step])
                    self.assertEqual(implicit["manifest"]["metadata"]["preset"], implicit["preset"])
                    for field in SELECTION_FIELDS:
                        self.assertEqual(implicit["manifest"][field], explicit["manifest"][field])
                    self.assertTrue(implicit["dry_run"])
                    self.assertEqual(implicit["manifest"]["metadata"]["provider"], "chase-sim")
                    self.assertTrue(
                        implicit["activation"].endswith(f"{VEHICLE}/bundle/runtime/{step}/active.json")
                    )
            self.assertFalse((root / VEHICLE / "bundle").exists())

    def test_every_named_preset_stages_what_it_names(self) -> None:
        """Regression: only memory's update checked that a staged preset
        carries the plugins, specs and configs its catalog entry names."""

        with self._runtime():
            for step, presets in STEP_PRESETS.items():
                for preset in presets:
                    with self.subTest(step=step, preset=preset):
                        payload = self._update(step, "--preset", preset, "--dry-run")
                        expected = preset_activation(step, preset).to_payload()
                        self.assertEqual(payload["preset"], preset)
                        self.assertEqual(payload["plugins"], expected["plugins"])
                        for field in SELECTION_FIELDS:
                            self.assertEqual(payload["manifest"][field], expected[field])

    def test_a_plugin_list_is_labeled_with_the_preset_it_equals_else_custom(self) -> None:
        """Regression: a plugin list matching a preset was labeled by that
        preset in memory's update only."""

        with self._runtime():
            for step in SELECTABLE_STEPS:
                with self.subTest(step=step):
                    plugins = STEP_PRESETS[step][DEFAULT_STEP_PRESETS[step]]["plugins"]
                    payload = self._update(
                        step, *(arg for plugin in plugins for arg in ("--plugin", plugin)), "--dry-run"
                    )
                    self.assertEqual(payload["preset"], DEFAULT_STEP_PRESETS[step])
            custom = self._update(
                "perception", "--plugin", "frame", "--plugin", "classical_regions", "--dry-run"
            )
        self.assertEqual(custom["preset"], "custom")
        self.assertEqual(custom["manifest"]["plugins"], ["frame", "classical_regions"])

    def test_a_plugin_list_keeps_staged_configs_and_a_preset_restores_the_packaged_ones(self) -> None:
        """Regression: a plugin list kept tuned configs for perception only,
        and memory and proposal lists reset them to the catalog defaults."""

        # Settings differ by step; selection and configuration precedence do not.
        cases = (
            ("perception", "multi_obstruction", "multi_obstruction_tracks", "max_tracks", 1),
            ("memory", "recency_ledger", "bounded_evidence", "max_records", 1),
            ("proposal", "obstruction_avoidance", "avoid_recent_obstruction", "steer_magnitude", 0.25),
        )
        with self._runtime() as root:
            for step, preset, plugin_id, setting, value in cases:
                with self.subTest(step=step):
                    recipe = self._update(step, "--preset", preset)
                    path = bundle_activation_path(host_bundle(VEHICLE, root), step)
                    tuned = json.loads(path.read_text(encoding="utf-8"))
                    self.assertNotEqual(tuned["plugin_configs"][plugin_id][setting], value)
                    tuned["plugin_configs"][plugin_id][setting] = value
                    path.write_text(json.dumps(tuned), encoding="utf-8")
                    flags = (arg for plugin in tuned["plugins"] for arg in ("--plugin", plugin))
                    selected = self._update(step, *flags)
                    self.assertEqual(selected["manifest"]["plugin_configs"], tuned["plugin_configs"])
                    restored = self._update(step, "--preset", preset)
                    self.assertEqual(
                        restored["manifest"]["plugin_configs"], recipe["manifest"]["plugin_configs"]
                    )
                    staged = json.loads(path.read_text(encoding="utf-8"))
                    self.assertEqual(staged["metadata"]["preset"], preset)

    def test_a_preset_with_plugins_and_unknown_ids_are_rejected_before_staging(self) -> None:
        """Regression: memory and perception repeated these checks in their own
        update paths, apart from the argparse group every step now shares."""

        with self._runtime() as root:
            for step in SELECTABLE_STEPS:
                with self.subTest(step=step):
                    update = ("hosts", "stage", step, "--name", VEHICLE)
                    preset = DEFAULT_STEP_PRESETS[step]
                    plugin = STEP_PRESETS[step][preset]["plugins"][0]
                    code, _, stderr = _invoke(*update, "--preset", preset, "--plugin", plugin)
                    self.assertEqual(code, 2)
                    self.assertIn("not allowed with argument", stderr)
                    code, _, stderr = _invoke(*update, "--preset", "nope")
                    self.assertEqual(code, 2)
                    self.assertIn("invalid choice: 'nope'", stderr)
                    for name in STEP_PRESETS[step]:
                        self.assertIn(name, stderr)
                    code, stdout, _ = _invoke(*update, "--plugin", "no_such_plugin")
                    self.assertEqual(code, 2)
                    self.assertIn(f"unknown {step} plugin(s) no_such_plugin", stdout)
            self.assertFalse((root / VEHICLE / "bundle").exists())

    def test_every_step_returns_one_payload_and_records_the_same_provenance(self) -> None:
        """Regression: perception returned ``vehicle_perception_update_v0`` and
        memory ``vehicle_memory_update_v1``, each with its own fields."""

        with self._runtime() as root:
            payloads = {step: self._update(step) for step in SELECTABLE_STEPS}
            bundle = host_bundle(VEHICLE, root)
            documents = {
                step: json.loads(bundle_activation_path(bundle, step).read_text(encoding="utf-8"))
                for step in SELECTABLE_STEPS
            }

        perception = documents["perception"]["metadata"]
        for step in SELECTABLE_STEPS:
            with self.subTest(step=step):
                self.assertEqual(payloads[step]["schema"], "host_step_update")
                self.assertEqual(set(payloads[step]), set(payloads["perception"]))
                metadata = documents[step]["metadata"]
                self.assertEqual(set(metadata), set(perception))
                self.assertEqual(set(metadata["controller_bundle"]), set(CONTROLLER_BUNDLE_KEYS))
                for key in ("root_dir", "autonomy_dir", "implementations_dir", "runtime_dir"):
                    self.assertEqual(metadata["controller_bundle"][key], perception["controller_bundle"][key])
                self.assertEqual(
                    metadata["controller_bundle"]["release"]["tree_sha256"],
                    perception["controller_bundle"]["release"]["tree_sha256"],
                )
                for key in ("host_name", "provider", "vehicle_kind", "runtime"):
                    self.assertEqual(metadata[key], perception[key])
                for key in ("source_dir", "workspace_source_dir", "preset_description"):
                    self.assertNotIn(key, metadata)
                self.assertEqual(metadata["preset"], DEFAULT_STEP_PRESETS[step])

    def test_inspect_restores_a_recorded_selection_and_a_preset_overrides_it(self) -> None:
        """Regression: a recording whose proposal was disabled replayed the
        default proposal, while perception and memory replayed empty."""

        with image_source(1) as root:
            code, stdout, stderr = _invoke("plugins", "inspect", "proposal", str(root), "--json")
            self.assertEqual(code, 0, stdout + stderr)
            original = json.loads(stdout)
            for step in SELECTABLE_STEPS:
                with self.subTest(step=step):
                    recorded = copy.deepcopy(original)
                    # A one-plugin step cannot be left empty by a recording.
                    clearable = step in CLEARABLE_STEPS
                    for frame in recorded["frames"]:
                        if clearable:
                            frame["steps"][step]["plugins"] = []
                            frame["steps"][step]["metadata"]["preset"] = "custom"
                    write_manifest(root, recorded)
                    inspect = ("plugins", "inspect", step, str(root), "--json")
                    code, stdout, stderr = _invoke(*inspect)
                    self.assertEqual(code, 0, stdout + stderr)
                    if clearable:
                        self.assertEqual(json.loads(stdout)["frames"][0]["steps"][step]["plugins"], [])
                    code, stdout, stderr = _invoke(*inspect, "--preset", DEFAULT_STEP_PRESETS[step])
                    self.assertEqual(code, 0, stdout + stderr)
                    overridden = json.loads(stdout)["frames"][0]["steps"][step]
                    fresh = selection_activation(step).to_payload()
                    for field in SELECTION_FIELDS:
                        self.assertEqual(overridden[field], fresh[field])
                    self.assertEqual(overridden["metadata"]["preset"], DEFAULT_STEP_PRESETS[step])

    def test_the_workbench_applies_the_selection_an_update_stages(self) -> None:
        """Regression: the workbench resolved its default selections through
        ``workbench_plugins.step_selection``, apart from the update path."""

        with image_source(1) as root, self._runtime():
            for step in SELECTABLE_STEPS:
                with self.subTest(step=step):
                    staged = self._update(step, "--dry-run")
                    for activations in (None, {step: selection_activation(step)}):
                        runner = ImageReplayRunner(root, activations=activations, cadence_ms=0, loop=False)
                        runner.start()
                        state = runner.wait(10)
                        self.assertEqual(state["phase"], "completed")
                        pipeline = state["machine_detail"]["pipeline"]
                        self.assertEqual(state[f"active_{step}_plugin_ids"], staged["manifest"]["plugins"])
                        self.assertEqual(pipeline[f"{step}_preset"], staged["preset"])
                        self.assertEqual(
                            pipeline[f"{step}_plugin_report"]["applied_plugin_ids"],
                            staged["manifest"]["plugins"],
                        )


if __name__ == "__main__":
    unittest.main()
