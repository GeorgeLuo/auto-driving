from __future__ import annotations

import tempfile
import unittest

from autonomy.decision import ActivatedMemoryStep, DecisionFrameContext, Observation, read_memory_activation
from autonomy.decision.selection import memory_plugin_manager
from tests.autonomy.decision.memory_activation_fixtures import _RecordingMemory, _valid_payload, _write_payload


class _OrderedMemory(_RecordingMemory):
    def update(self, context, observation):
        context.shared_memory.setdefault("execution_order", []).append(self.implementation_id)
        previous = context.shared_memory.get("decision.snapshot")
        context.shared_memory.setdefault("previous_outputs", []).append(
            previous.implementation_id if previous else None
        )
        return super().update(context, observation)


SPEC = "tests.autonomy.decision.test_memory_selection:_OrderedMemory"


def _manager():
    return memory_plugin_manager(
        {"first": SPEC, "second": SPEC},
        {name: {"implementation_id": name} for name in ("first", "second")},
    )


class MemorySelectionTests(unittest.TestCase):
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
