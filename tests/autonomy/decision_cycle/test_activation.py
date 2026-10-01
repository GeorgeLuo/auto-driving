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
    read_step_activation_if_present,
    step_activation,
    step_activation_path,
    write_step_activation,
)
from autonomy.plugins import PluginManagementError

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
                path = _write(tmp, _payload(step, metadata={"algorithm": "preset"}))
                activation = read_step_activation(path, step)
                self.assertEqual(activation.step, step)
                self.assertEqual(activation.plugins, ("first",))
                self.assertEqual(activation.source_path, path)
                self.assertEqual(activation.metadata, {"algorithm": "preset"})
                manager = activation.plugin_manager()
                self.assertEqual(manager.step, step)
                self.assertEqual(manager.available_ids, ("first", "second"))
                self.assertEqual(manager.selected_ids, ("first",))

    def test_invalid_documents_are_rejected(self) -> None:
        cases = {
            "not an object": [],
            "old schema": _payload(schema="automa_memory_activation_v0"),
            "unknown key": _payload(implementation_id="first"),
            "unknown step": _payload("remember"),
            "plugins not a list": _payload(plugins="first"),
            "duplicate plugins": _payload(plugins=["first", "first"]),
            "specs not a map": _payload(plugin_specs=["first"]),
            "config not an object": _payload(plugin_configs={"first": []}),
            "metadata not an object": _payload(metadata=[]),
        }
        for name, payload in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(ValueError):
                    read_step_activation(_write(tmp, payload))
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "not 'memory'"):
                read_step_activation(_write(tmp, _payload("proposal")), "memory")
            with self.assertRaises(PluginManagementError):
                read_step_activation(_write(tmp, _payload(plugins=["ghost"])))

    def test_missing_documents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory" / "active.json"
            with self.assertRaisesRegex(FileNotFoundError, "memory activation is missing"):
                read_step_activation(path, "memory")
            self.assertIsNone(read_step_activation_if_present(path, "memory"))

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
