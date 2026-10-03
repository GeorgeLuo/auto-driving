"""Every step is activated by the same document shape."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from autonomy.decision_cycle.activation import (
    STEP_ACTIVATION_SCHEMA,
    STEPS,
    activation_generation_id,
    read_step_activation,
    step_activation,
    step_activation_path,
    write_step_activation,
)

SPEC = "tests.autonomy.decision_cycle.memory.activation_fixtures:_RecordingMemory"


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
                manager = activation.plugin_manager()
                self.assertEqual(manager.step, step)
                self.assertEqual(manager.available_ids, ("first", "second"))
                self.assertEqual(manager.selected_ids, ("first",))

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


if __name__ == "__main__":
    unittest.main()
