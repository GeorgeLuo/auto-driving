from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from autonomy.decision_cycle.activation import STEP_ACTIVATION_SCHEMA, read_step_activation, step_activation
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.memory.plugin import MemoryPluginContract
from autonomy.decision_cycle.memory.runner import MemoryRunner
from autonomy.decision_cycle.observation.publication import OBSERVATION_KEY
from autonomy.decision_cycle.observation.values import Observation
from tests.shared.autonomy.decision_cycle.memory.activation_fixtures import _RecordingMemory


class _OnceMemory(_RecordingMemory):
    constructions = 0

    def __init__(self, **config):
        type(self).constructions += 1
        super().__init__(**config)


class _OrderedMemory(_RecordingMemory):
    def update(self, context):
        context.shared_memory.setdefault("execution_order", []).append(self.plugin_id)
        context.shared_memory.setdefault("previous_outputs", []).append(
            context.shared_memory.get("last_writer")
        )
        context.shared_memory["last_writer"] = self.plugin_id
        super().update(context)


class _NotMemory:
    """Constructs, but offers no ``update`` or ``reset``."""

    contract = MemoryPluginContract()

    def __init__(self, **config):
        self.plugin_id = config["plugin_id"]


SPEC = "tests.deterministic.autonomy.decision_cycle.memory.test_selection:_OrderedMemory"
ONCE = "tests.deterministic.autonomy.decision_cycle.memory.test_selection:_OnceMemory"


def _runner(*plugin_ids: str, specs: dict[str, str] | None = None) -> MemoryRunner:
    chosen = specs or {plugin_id: SPEC for plugin_id in plugin_ids}
    return MemoryRunner.from_activation(step_activation(
        "memory",
        list(plugin_ids),
        {plugin_id: chosen[plugin_id] for plugin_id in plugin_ids},
        {plugin_id: {"plugin_id": plugin_id} for plugin_id in plugin_ids},
    ))


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
            self.assertEqual(activation.plugins, ("second",))
            step = MemoryRunner.from_activation(activation)
            self.assertEqual(set(step.reset({})), {"second.state"})
            self.assertEqual(step.status()["activation"], str(path))
            self.assertEqual(step.plugin_ids, ("second",))

            path.write_text(json.dumps({
                "schema": STEP_ACTIVATION_SCHEMA,
                "step": "memory",
                "plugins": [],
                "plugin_specs": {"first": SPEC},
            }), encoding="utf-8")
            empty = MemoryRunner.from_activation(read_step_activation(path, "memory"))
            self.assertEqual(empty.plugin_ids, ())
            self.assertEqual(empty.report()["plugins"], [])
            self.assertEqual(step.plugin_ids, ("second",))

    def test_repeated_updates_keep_the_constructed_instance(self):
        step = _runner("second")
        plugin = step.plugins["second"]
        step.update(DecisionFrameContext("frame-1", 1, 100, shared_memory={}))
        step.update(DecisionFrameContext("frame-2", 2, 200, shared_memory={}))
        self.assertIs(step.plugins["second"], plugin)
        self.assertEqual(step.status()["plugin_ids"], ["second"])

    def test_plugins_run_in_order_and_later_plugins_see_earlier_writes(self):
        step = _runner("first", "second")
        shared = {}
        context = DecisionFrameContext("frame-1", 1, 100, shared_memory=shared)
        context.shared_memory[OBSERVATION_KEY] = Observation("obs-1", 90, {})
        report = step.update(context)
        self.assertEqual(shared["execution_order"], ["first", "second"])
        self.assertEqual(shared["previous_outputs"], [None, "first"])
        self.assertEqual(shared["last_writer"], "second")
        self.assertEqual([item["plugin_id"] for item in report["plugins"]], ["first", "second"])
        self.assertEqual([item["update_count"] for item in step.status()["plugins"]], [1, 1])

    def test_reset_without_a_map_resets_in_the_last_map(self):
        step = _runner("first")
        shared = {}
        context = DecisionFrameContext("frame-1", 1, 100, shared_memory=shared)
        context.shared_memory[OBSERVATION_KEY] = Observation("obs-1", 90, {})
        step.update(context)
        step.reset()
        self.assertEqual(shared["first.state"], {"epoch": 2, "records": ()})
        self.assertEqual(step.status()["plugins"][0]["state"]["epoch_id"], "epoch-2")

    def test_a_new_selection_is_a_new_runner(self):
        step = _runner("first", "second")
        first = step.plugins["first"]
        context = DecisionFrameContext("frame-1", 1, 100, shared_memory={})
        step.update(context)
        replacement = _runner("second")
        replacement.update(context)
        self.assertIs(step.plugins["first"], first)
        self.assertEqual(first.reset_count, 0)
        self.assertEqual(replacement.plugin_ids, ("second",))
        self.assertIsNot(replacement.plugins["second"], step.plugins["second"])

    def test_construction_builds_each_plugin_once_and_a_bad_one_is_not_published(self):
        _OnceMemory.constructions = 0
        step = _runner("second", specs={"second": ONCE})
        self.assertEqual(_OnceMemory.constructions, 1)
        self.assertEqual(step.plugin_ids, ("second",))
        with self.assertRaises(ModuleNotFoundError):
            _runner("missing", specs={
                "missing": "tests.deterministic.autonomy.decision_cycle.memory.no_such_module:Nope",
            })
        with self.assertRaisesRegex(TypeError, "does not satisfy MemoryPlugin"):
            _runner("not_memory", specs={"not_memory": f"{__name__}:_NotMemory"})
        self.assertEqual(step.plugin_ids, ("second",))
        self.assertEqual(_OnceMemory.constructions, 1)
