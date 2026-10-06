from __future__ import annotations

from autonomy.plugins import PluginManager

import json
import tempfile
import unittest
from pathlib import Path

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.memory.runner import MemoryRunner
from autonomy.decision_cycle.activation import STEP_ACTIVATION_SCHEMA, read_step_activation
from tests.autonomy.decision_cycle.memory.activation_fixtures import _RecordingMemory


class _OnceMemory(_RecordingMemory):
    constructions = 0

    def __init__(self, **config):
        type(self).constructions += 1
        super().__init__(**config)



class _OrderedMemory(_RecordingMemory):
    def update(self, context, observation):
        context.shared_memory.setdefault("execution_order", []).append(self.plugin_id)
        context.shared_memory.setdefault("previous_outputs", []).append(
            context.shared_memory.get("last_writer")
        )
        context.shared_memory["last_writer"] = self.plugin_id
        super().update(context, observation)


class _NotMemory:
    """Constructs, but offers no ``update`` or ``reset``."""

    def __init__(self, **config):
        self.plugin_id = config["plugin_id"]


SPEC = "tests.autonomy.decision_cycle.memory.test_selection:_OrderedMemory"


def _manager():
    return PluginManager.from_specs(
        "memory",
        {"first": SPEC, "second": SPEC},
        {name: {"plugin_id": name} for name in ("first", "second")},
    )


class MemorySelectionTests(unittest.TestCase):
    def test_step_activation_selects_from_its_specs(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "active.json"
            path.write_text(json.dumps({
                "schema": STEP_ACTIVATION_SCHEMA,
                "step": "memory",
                "plugins": ["second"],
                "plugin_specs": {"first": SPEC, "second": SPEC},
                "plugin_configs": {"second": {"plugin_id": "second"}},
            }), encoding="utf-8")
            activation = read_step_activation(path, "memory")
            self.assertEqual(activation.plugin_manager().available_ids, ("first", "second"))
            self.assertEqual(activation.plugins, ("second",))
            step = MemoryRunner.from_activation(activation)
            self.assertEqual(set(step.reset({})), {"second.state"})
            self.assertEqual(step.status()["activation"], str(path))

            path.write_text(json.dumps({
                "schema": STEP_ACTIVATION_SCHEMA,
                "step": "memory",
                "plugins": [],
                "plugin_specs": {"first": SPEC},
            }), encoding="utf-8")
            step = MemoryRunner.from_activation(read_step_activation(path, "memory"))
            self.assertEqual(step.plugin_ids, ())
            self.assertEqual(step.status()["available_plugins"], ["first"])
            self.assertEqual(step.report()["plugins"], [])

    def test_status_reports_unselected_catalog_plugins_and_can_enable_them(self):
        manager = _manager()
        step = MemoryRunner(plugin_manager=manager)
        self.assertEqual(step.status()["available_plugins"], ["first", "second"])
        self.assertEqual(step.plugins, {})
        manager.add("second")
        report = step.update(DecisionFrameContext("frame-1", 1, 100, shared_memory={}), None)
        self.assertEqual(report["plugins"][0]["plugin_id"], "second")
        self.assertEqual(step.status()["available_plugins"], ["first", "second"])
        manager.remove("second")
        step.update(DecisionFrameContext("frame-2", 2, 200, shared_memory={}), None)
        self.assertEqual(step.status()["available_plugins"], ["first", "second"])
        self.assertEqual(step.plugins, {})

    def test_manager_selection_runs_in_order_and_later_plugins_see_earlier_writes(self):
        manager = _manager()
        manager.select(["first", "second"])
        step = MemoryRunner(plugin_manager=manager)
        shared = {}
        report = step.update(
            DecisionFrameContext("frame-1", 1, 100, shared_memory=shared),
            Observation("obs-1", 90, {}),
        )
        self.assertEqual(shared["execution_order"], ["first", "second"])
        self.assertEqual(shared["previous_outputs"], [None, "first"])
        self.assertEqual(shared["last_writer"], "second")
        self.assertEqual(
            [item["plugin_id"] for item in report["plugins"]], ["first", "second"]
        )
        self.assertEqual([item["update_count"] for item in step.status()["plugins"]], [1, 1])

    def test_reset_without_a_map_resets_in_the_last_map(self):
        manager = _manager()
        manager.select(["first"])
        step = MemoryRunner(plugin_manager=manager)
        shared = {}
        step.update(DecisionFrameContext("frame-1", 1, 100, shared_memory=shared), Observation("obs-1", 90, {}))
        step.reset()
        self.assertEqual(shared["first.state"], {"epoch": 2, "records": ()})
        self.assertEqual(step.status()["plugins"][0]["state"]["epoch_id"], "epoch-2")

    def test_selection_changes_reuse_retained_plugins_and_reset_removed_plugins(self):
        manager = _manager()
        manager.select(["first", "second"])
        step = MemoryRunner(plugin_manager=manager)
        first, second = step.plugins.values()
        context = DecisionFrameContext("frame-1", 1, 100, shared_memory={})
        step.update(context, None)
        manager.select(["second", "first"])
        step.update(context, None)
        self.assertEqual(step.plugins, {"second": second, "first": first})
        self.assertEqual(list(step.plugins), ["second", "first"])
        manager.remove("first")
        step.update(context, None)
        self.assertEqual(first.reset_count, 1)
        self.assertEqual(step.plugins, {"second": second})
        self.assertEqual(second.reset_count, 0)
        manager.add("first")
        step.update(context, None)
        self.assertIsNot(step.plugins["first"], first)
        self.assertIs(step.plugins["second"], second)
        self.assertEqual(step.plugins["first"].update_count, 1)

    def test_empty_selection_can_be_enabled_then_disabled_between_cycles(self):
        manager = _manager()
        step = MemoryRunner(plugin_manager=manager)
        shared = {}
        context = DecisionFrameContext("frame-1", 1, 100, shared_memory=shared)
        self.assertEqual(step.update(context, None)["plugins"], [])
        manager.add("first")
        self.assertEqual(step.update(context, None)["plugins"][0]["plugin_id"], "first")
        manager.remove("first")
        self.assertEqual(step.update(context, None)["plugins"], [])
        self.assertEqual(step.status()["plugin_ids"], [])

    def test_prepare_selection_constructs_a_replacement_once(self):
        _OnceMemory.constructions = 0
        manager = PluginManager.from_specs(
            "memory",
            {"first": SPEC, "second": f"{__name__}:_OnceMemory"},
            {
                "first": {"plugin_id": "first"},
                "second": {"plugin_id": "second"},
            },
        )
        manager.select(["first"])
        step = MemoryRunner(plugin_manager=manager)
        original = step.plugins["first"]
        manager.select(["second"])

        step.prepare_selection()

        self.assertEqual(_OnceMemory.constructions, 1)
        self.assertEqual(step.plugins, {"first": original})
        self.assertEqual(original.reset_count, 0)
        step.commit_selection()
        self.assertEqual(_OnceMemory.constructions, 1)
        self.assertEqual(step.plugin_ids, ("second",))
        self.assertEqual(step.plugins["second"].implementation.plugin_id, "second")
        self.assertEqual(original.reset_count, 1)

    def test_a_selection_that_cannot_load_raises_and_the_next_good_edit_recovers(self):
        manager = PluginManager.from_specs(
            "memory",
            {
                "first": SPEC,
                "missing": "tests.autonomy.decision_cycle.memory.no_such_module:Nope",
                "not_memory": f"{__name__}:_NotMemory",
            },
            {name: {"plugin_id": name} for name in ("first", "missing", "not_memory")},
        )
        manager.select(["first"])
        step = MemoryRunner(plugin_manager=manager)
        first = step.plugins["first"]
        context = DecisionFrameContext("frame-1", 1, 100, shared_memory={})
        step.update(context, None)

        # A selection that cannot load stops the step with the loader's own error.
        manager.select(["missing"])
        with self.assertRaisesRegex(ModuleNotFoundError, "no_such_module"):
            step.update(context, None)
        manager.select(["not_memory"])
        with self.assertRaisesRegex(TypeError, "does not satisfy MemoryPlugin"):
            step.update(context, None)
        # Nothing was published: the plugin that was applied is still the one that runs.
        self.assertEqual(step.plugins, {"first": first})

        manager.select(["first"])
        step.update(context, None)
        self.assertIs(step.plugins["first"], first)
        self.assertEqual(first.update_count, 2)


