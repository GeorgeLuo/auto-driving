"""Every step stages its default through ``hosts update``; replay and the workbench select the same way."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from tests.support.runtime_fixtures import write_host_fixture

from autonomy.decision_cycle.activation import STEPS
from autonomy.decision_cycle.steps import CLEARABLE_STEPS
from cli.automa_cli.hosts.step_activations import (
    CONTROLLER_BUNDLE_KEYS,
    bundle_activation_path,
    host_bundle,
    read_bundle_activation,
    stage_activation,
)
from cli.automa_cli.workbench.runner import ImageReplayRunner
from implementations.decision_cycle.catalog import (
    DEFAULT_STEP_PRESETS,
    PRESET_STEPS,
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
    def _update(self, root: Path) -> dict:
        result = invoke_automa("hosts", "update", "--name", VEHICLE, "--json", runtime_root=root, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_an_update_replaces_every_staged_selection_with_its_default(self) -> None:
        """A step staged before ``hosts stage`` was removed, or left unreadable,
        would otherwise keep its selection, or block the update, with no command to change it."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_host_fixture(root, VEHICLE)
            bundle = host_bundle(VEHICLE, root)
            stage_activation(bundle, selection_activation("perception", plugins=["frame"]), host_name=VEHICLE)
            stage_activation(bundle, selection_activation("proposal", preset="obstruction_avoidance"), host_name=VEHICLE)
            unreadable = bundle_activation_path(bundle, "memory")
            unreadable.parent.mkdir(parents=True, exist_ok=True)
            unreadable.write_text("{", encoding="utf-8")

            payload = self._update(root)
            for step in STEPS:
                with self.subTest(step=step):
                    default = selection_activation(step).to_payload()
                    staged = read_bundle_activation(bundle, step).to_payload()
                    for field in SELECTION_FIELDS:
                        self.assertEqual(staged[field], default[field])
                    self.assertEqual(payload["steps"][step], default["plugins"])
            self.assertEqual(payload["presets"], DEFAULT_STEP_PRESETS)

    def test_an_update_records_the_same_provenance_on_every_step(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_host_fixture(root, VEHICLE)
            self._update(root)
            bundle = host_bundle(VEHICLE, root)
            documents = {
                step: json.loads(bundle_activation_path(bundle, step).read_text(encoding="utf-8"))
                for step in STEPS
            }

        perception = documents["perception"]["metadata"]
        for step in STEPS:
            with self.subTest(step=step):
                metadata = documents[step]["metadata"]
                self.assertEqual(set(metadata) - {"preset"}, set(perception) - {"preset"})
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
                self.assertEqual(metadata.get("preset"), DEFAULT_STEP_PRESETS.get(step))

    def test_inspect_restores_a_recorded_selection_and_a_preset_overrides_it(self) -> None:
        """Regression: a recording whose proposal was disabled replayed the
        default proposal, while perception and memory replayed empty."""

        with image_source(1) as root:
            code, stdout, stderr = _invoke("plugins", "inspect", "proposal", str(root), "--json")
            self.assertEqual(code, 0, stdout + stderr)
            original = json.loads(stdout)
            for step in PRESET_STEPS:
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

        with image_source(1) as root:
            for step in PRESET_STEPS:
                with self.subTest(step=step):
                    staged = selection_activation(step).to_payload()
                    for activations in (None, {step: selection_activation(step)}):
                        runner = ImageReplayRunner(root, activations=activations, cadence_ms=0, loop=False)
                        runner.start()
                        state = runner.wait(10)
                        self.assertEqual(state["phase"], "completed")
                        pipeline = state["machine_detail"]["pipeline"]
                        self.assertEqual(state[f"active_{step}_plugin_ids"], staged["plugins"])
                        self.assertEqual(pipeline[f"{step}_preset"], DEFAULT_STEP_PRESETS[step])
                        self.assertEqual(
                            pipeline[f"{step}_plugin_report"]["applied_plugin_ids"],
                            staged["plugins"],
                        )


if __name__ == "__main__":
    unittest.main()
