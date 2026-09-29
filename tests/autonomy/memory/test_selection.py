from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from autonomy.decision import DecisionFrameContext, Observation
from autonomy.memory import ActivatedMemoryStep, read_memory_activation
from autonomy.memory.activation import memory_manager_from_activation, memory_selection_config
from autonomy.memory.selection import memory_plugin_manager
from tests.autonomy.memory.activation_fixtures import _RecordingMemory, _valid_payload, _write_payload


class _OrderedMemory(_RecordingMemory):
    def update(self, context, observation):
        context.shared_memory.setdefault("execution_order", []).append(self.implementation_id)
        previous = context.shared_memory.get("decision.snapshot")
        context.shared_memory.setdefault("previous_outputs", []).append(
            previous.implementation_id if previous else None
        )
        return super().update(context, observation)


SPEC = "tests.autonomy.memory.test_selection:_OrderedMemory"


def _manager():
    return memory_plugin_manager(
        {"first": SPEC, "second": SPEC},
        {name: {"implementation_id": name} for name in ("first", "second")},
    )


class MemorySelectionTests(unittest.TestCase):
    def test_explicit_activation_normalizes_definitions_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "active.json"
            path.write_text(json.dumps({
                "schema": "automa_memory_activation_v0",
                "memory": {
                    "plugins": ["second"],
                    "plugin_specs": {"first": SPEC, "second": SPEC},
                    "plugin_configs": {"second": {"implementation_id": "second"}},
                    # Legacy staging fields do not constrain the selected plugin.
                    "implementation_id": "stale",
                    "implementation_spec": "not.installed:Missing",
                    "implementation_config": {"max_serialized_bytes": 1},
                },
            }), encoding="utf-8")
            activation = read_memory_activation(path)
            self.assertEqual(
                [definition.plugin_id for definition in activation.available_definitions],
                ["first", "second"],
            )
            self.assertEqual(
                [definition.plugin_id for definition in activation.selected_definitions],
                ["second"],
            )
            self.assertEqual(activation.implementation_spec, SPEC)
            activation.payload["memory"]["plugin_specs"]["second"] = "not.installed:Missing"
            self.assertEqual(memory_selection_config(activation)["plugin_specs"]["second"], SPEC)
            step = ActivatedMemoryStep(activation)
            self.assertEqual(step.reset({}).implementation_id, "second")
            self.assertEqual(memory_manager_from_activation(activation).available_ids, ("first", "second"))

            # The same selection also works without any legacy fields.
            plugin_only = {
                "schema": "automa_memory_activation_v0",
                "memory": {
                    "plugins": ["second"],
                    "plugin_specs": {"second": SPEC},
                    "plugin_configs": {"second": {"implementation_id": "second"}},
                },
            }
            path.write_text(json.dumps(plugin_only), encoding="utf-8")
            step = ActivatedMemoryStep(read_memory_activation(path))
            self.assertEqual(step.reset({}).implementation_id, "second")

    def test_plugin_only_activation_supports_an_empty_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "active.json"
            path.write_text(json.dumps({
                "schema": "automa_memory_activation_v0",
                "memory": {"plugins": [], "plugin_specs": {"first": SPEC}},
            }), encoding="utf-8")
            activation = read_memory_activation(path)
            step = ActivatedMemoryStep(activation)
            self.assertEqual(step.plugin_ids, ())
            self.assertEqual(step.status()["available_plugins"], ["first"])
            self.assertIsNone(step.snapshot())

    def test_status_reports_unselected_catalog_plugins_and_can_enable_them(self):
        manager = _manager()
        step = ActivatedMemoryStep(plugin_manager=manager)
        self.assertEqual(step.status()["available_plugins"], ["first", "second"])
        self.assertEqual(step.plugins, ())
        manager.add("second")
        snapshot = step.update(DecisionFrameContext("frame-1", 1, 100, shared_memory={}), None)
        self.assertEqual(snapshot.implementation_id, "second")
        self.assertEqual(step.status()["available_plugins"], ["first", "second"])
        manager.remove("second")
        step.update(DecisionFrameContext("frame-2", 2, 200, shared_memory={}), None)
        self.assertEqual(step.status()["available_plugins"], ["first", "second"])
        self.assertEqual(step.plugins, ())

    def test_manager_selection_runs_in_order_and_last_output_reaches_decision(self):
        manager = _manager()
        manager.select(["first", "second"])
        step = ActivatedMemoryStep(plugin_manager=manager)
        shared = {}
        snapshot = step.update(
            DecisionFrameContext("frame-1", 1, 100, shared_memory=shared),
            Observation("obs-1", 90, {}),
        )
        self.assertEqual(shared["execution_order"], ["first", "second"])
        self.assertEqual(shared["previous_outputs"], [None, "first"])
        self.assertEqual(snapshot.implementation_id, "second")
        self.assertEqual(shared["decision.snapshot"], snapshot)
        self.assertEqual([item["update_count"] for item in step.status()["plugins"]], [1, 1])

    def test_selection_changes_reuse_retained_plugins_and_reset_removed_plugins(self):
        manager = _manager()
        manager.select(["first", "second"])
        step = ActivatedMemoryStep(plugin_manager=manager)
        first, second = step.plugins
        context = DecisionFrameContext("frame-1", 1, 100, shared_memory={})
        step.update(context, None)
        manager.select(["second", "first"])
        step.update(context, None)
        self.assertIs(step.plugins[0], second)
        self.assertIs(step.plugins[1], first)
        manager.remove("first")
        step.update(context, None)
        self.assertEqual(first.reset_count, 1)
        self.assertIs(step.plugins[0], second)
        self.assertEqual(second.reset_count, 0)
        manager.add("first")
        step.update(context, None)
        self.assertIsNot(step.plugins[1], first)
        self.assertIs(step.plugins[0], second)
        self.assertEqual(step.plugins[1].update_count, 1)

    def test_empty_selection_can_be_enabled_then_disabled_between_cycles(self):
        manager = _manager()
        step = ActivatedMemoryStep(plugin_manager=manager)
        shared = {}
        context = DecisionFrameContext("frame-1", 1, 100, shared_memory=shared)
        self.assertIsNone(step.update(context, None))
        manager.add("first")
        self.assertEqual(step.update(context, None).implementation_id, "first")
        manager.remove("first")
        self.assertIsNone(step.update(context, None))
        self.assertNotIn("decision.snapshot", shared)
        self.assertEqual(step.status()["plugin_ids"], [])

    def test_selection_id_stays_distinct_from_implementation_id(self):
        manager = memory_plugin_manager({"ledger": SPEC}, {"ledger": {}})
        manager.select(["ledger"])
        step = ActivatedMemoryStep(plugin_manager=manager)
        self.assertEqual(step.plugin_ids, ("ledger",))
        self.assertEqual(step.plugins[0].implementation.implementation_id, "recording_test")
        plugin_status = step.status()["plugins"][0]
        self.assertEqual(plugin_status["plugin_id"], "ledger")
        self.assertEqual(plugin_status["implementation_id"], "recording_test")
        self.assertEqual(step.snapshot().implementation_id, "recording_test")

    def test_legacy_activation_seeds_manager_and_explicit_selection_overrides_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = _valid_payload()
            activation = read_memory_activation(_write_payload(tmp, payload))
            self.assertEqual(ActivatedMemoryStep(activation).plugin_ids, ("recording_test",))
            payload["memory"].update({
                "plugins": ["first", "second"],
                "plugin_specs": {"first": SPEC, "second": SPEC},
                "plugin_configs": {name: {"implementation_id": name} for name in ("first", "second")},
            })
            activation = read_memory_activation(_write_payload(tmp, payload))
            step = ActivatedMemoryStep(activation)
            self.assertEqual(step.plugin_ids, ("first", "second"))
            self.assertEqual(step.reset({}).implementation_id, "second")
            self.assertEqual([item["reset_count"] for item in step.status()["plugins"]], [1, 1])
