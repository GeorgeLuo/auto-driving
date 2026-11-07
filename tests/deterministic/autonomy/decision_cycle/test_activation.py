"""Every step is activated by the same document shape."""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from autonomy.decision_cycle.activation import (
    STEP_ACTIVATION_SCHEMA,
    STEPS,
    activation_generation_id,
    read_step_activation,
    selected_definitions,
    step_activation,
    step_activation_path,
    write_step_activation,
)

SPEC = "tests.shared.autonomy.decision_cycle.memory.activation_fixtures:_RecordingMemory"


def _payload(step: str = "memory", **overrides) -> dict:
    payload = {
        "schema": STEP_ACTIVATION_SCHEMA,
        "step": step,
        "plugins": ["first"],
        "plugin_specs": {"first": SPEC, "second": SPEC},
        "plugin_configs": {"first": {"plugin_id": "first"}},
    }
    payload.update(overrides)
    return payload


def _write(root: str, payload: object) -> Path:
    path = Path(root) / "active.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class StepActivationTests(unittest.TestCase):
    def test_every_step_reads_the_same_document(self) -> None:
        for step in STEPS:
            with self.subTest(step=step), tempfile.TemporaryDirectory() as tmp:
                path = _write(tmp, _payload(step, metadata={"preset": "preset"}))
                activation = read_step_activation(path, step)
                self.assertEqual(activation.step, step)
                self.assertEqual(activation.plugins, ("first",))
                self.assertEqual(activation.source_path, path)
                self.assertEqual(activation.metadata, {"preset": "preset"})
                definitions = selected_definitions(activation)
                self.assertEqual(definitions[0].step, step)
                self.assertEqual(tuple(item.plugin_id for item in definitions), ("first",))

    def test_repeated_plugin_id_in_a_document_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "active.json"
            text = json.dumps(_payload())
            path.write_text(
                text.replace('"second": ', '"first": "other:Plugin", "second": '),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "repeats key 'first'"):
                read_step_activation(path, "memory")

    def test_write_round_trips_under_the_step_directory(self) -> None:
        activation = step_activation(
            "proposal",
            ["first"],
            {"first": SPEC},
            {"first": {"plugin_id": "first"}},
            metadata={"note": "kept"},
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = step_activation_path(Path(tmp), "proposal")
            self.assertEqual(path, Path(tmp) / "proposal" / "active.json")
            write_step_activation(path, activation)
            self.assertEqual(sorted(p.name for p in path.parent.iterdir()), ["active.json"])
            reread = read_step_activation(path, "proposal")
        self.assertEqual(reread.to_payload(), activation.to_payload())
        with self.assertRaises(ValueError):
            step_activation_path(Path("runtime"), "decision")

    def test_selection_is_detached_from_its_inputs(self) -> None:
        configs = {"first": {"plugin_id": "first", "nested": {"value": 1}}}
        activation = step_activation("memory", ["first"], {"first": SPEC}, configs)
        configs["first"]["nested"]["value"] = 2
        self.assertEqual(activation.plugin_configs["first"]["nested"]["value"], 1)
        payload = activation.to_payload()
        payload["plugin_configs"]["first"]["nested"]["value"] = 3
        self.assertEqual(activation.plugin_configs["first"]["nested"]["value"], 1)

    def test_generation_id_follows_content_not_metadata(self) -> None:
        first = step_activation("proposal", ["first"], {"first": SPEC})
        tagged = step_activation("proposal", ["first"], {"first": SPEC}, metadata={"by": "cli"})
        other = step_activation("proposal", [], {"first": SPEC})
        generation = activation_generation_id({"proposal": first}, prefix="decision")
        self.assertTrue(generation.startswith("decision-"))
        self.assertEqual(generation, activation_generation_id({"proposal": tagged}, prefix="decision"))
        self.assertEqual(
            generation,
            activation_generation_id({"proposal": first.to_payload()}, prefix="decision"),
        )
        self.assertNotEqual(generation, activation_generation_id({"proposal": other}, prefix="decision"))

    def test_executable_identity_ignores_display_paths_and_unselected_definitions(self):
        activation = step_activation("proposal", ["first"], {"first": SPEC, "unused": "other:Plugin"},
                                     metadata={"host_name": "fixture", "preset": "one", "controller_bundle": {
                                         "root_dir": "/first/install", "release": {"tree_sha256": "a" * 64},
                                     }})
        relocated = replace(activation, plugin_specs={"first": SPEC, "unused": "new:Plugin"},
                            plugin_configs={"unused": {"value": 1}}, metadata={
                                "host_name": "fixture", "preset": "two", "plugin_revisions": {"unused": "changed"},
                                "controller_bundle": {"root_dir": "/another/install", "release": {
                                    "tree_sha256": "a" * 64, "created_at_ms": 200,
                                }},
                            })
        self.assertEqual(activation_generation_id({"proposal": activation}),
                         activation_generation_id({"proposal": relocated}))

    def test_every_executable_revision_and_selected_config_changes_the_identity(self):
        activation = step_activation("proposal", ["first"], {"first": SPEC}, metadata={
            "host_name": "fixture", "controller_bundle": {"release": {"tree_sha256": "a" * 64}},
        })
        original = activation_generation_id({"proposal": activation})
        for changed in (
            replace(activation, plugin_configs={"first": {"value": 1}}),
            replace(activation, plugin_specs={"first": "other:Plugin"}),
            replace(activation, metadata={**activation.metadata, "host_name": "another-host"}),
            replace(activation, metadata={**activation.metadata, "controller_bundle": {"release": {"tree_sha256": "b" * 64}}}),
        ):
            with self.subTest(activation=changed):
                self.assertNotEqual(original, activation_generation_id({"proposal": changed}))

    def test_upload_identity_uses_the_source_hash_and_not_the_install_directory(self):
        first = step_activation("proposal", ["first"], {"first": f"/first/{'a' * 64}.py:Plugin"})
        moved = replace(first, plugin_specs={"first": f"/another/{'a' * 64}.py:Plugin"})
        changed = replace(first, plugin_specs={"first": f"/another/{'b' * 64}.py:Plugin"})
        self.assertEqual(activation_generation_id({"proposal": first}), activation_generation_id({"proposal": moved}))
        self.assertNotEqual(activation_generation_id({"proposal": first}), activation_generation_id({"proposal": changed}))


if __name__ == "__main__":
    unittest.main()
