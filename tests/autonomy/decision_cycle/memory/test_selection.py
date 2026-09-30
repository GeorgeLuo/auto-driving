from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.memory.plugin_runner import PluginMemoryRunner
from autonomy.decision_cycle.memory.activation import (
    read_memory_activation,
    memory_manager_from_activation,
    memory_selection_config,
)
from autonomy.decision_cycle.memory.selection import memory_plugin_manager
from tests.autonomy.decision_cycle.memory.activation_fixtures import _RecordingMemory, _valid_payload, _write_payload


class _OnceMemory(_RecordingMemory):
    constructions = 0

    def __init__(self, **config):
        type(self).constructions += 1
        super().__init__(**config)


class _RejectMemory(_RecordingMemory):
    def __init__(self, **config):
        raise RuntimeError("rejected memory")


class _OrderedMemory(_RecordingMemory):
    def update(self, context, observation):
        context.shared_memory.setdefault("execution_order", []).append(self.implementation_id)
        context.shared_memory.setdefault("previous_outputs", []).append(
            context.shared_memory.get("last_writer")
        )
        context.shared_memory["last_writer"] = self.implementation_id
        super().update(context, observation)


SPEC = "tests.autonomy.decision_cycle.memory.test_selection:_OrderedMemory"


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
                    "implementation_config": {"fail_on_update": True},
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
            step = PluginMemoryRunner(activation)
            self.assertEqual(set(step.reset({})), {"second.state"})
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
            step = PluginMemoryRunner(read_memory_activation(path))
            self.assertEqual(set(step.reset({})), {"second.state"})

    def test_plugin_only_activation_supports_an_empty_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "active.json"
            path.write_text(json.dumps({
                "schema": "automa_memory_activation_v0",
                "memory": {"plugins": [], "plugin_specs": {"first": SPEC}},
            }), encoding="utf-8")
            activation = read_memory_activation(path)
            step = PluginMemoryRunner(activation)
            self.assertEqual(step.plugin_ids, ())
            self.assertEqual(step.status()["available_plugins"], ["first"])
            self.assertEqual(step.report()["plugins"], [])

    def test_status_reports_unselected_catalog_plugins_and_can_enable_them(self):
        manager = _manager()
        step = PluginMemoryRunner(plugin_manager=manager)
        self.assertEqual(step.status()["available_plugins"], ["first", "second"])
        self.assertEqual(step.plugins, ())
        manager.add("second")
        report = step.update(DecisionFrameContext("frame-1", 1, 100, shared_memory={}), None)
        self.assertEqual(report["plugins"][0]["implementation_id"], "second")
        self.assertEqual(step.status()["available_plugins"], ["first", "second"])
        manager.remove("second")
        step.update(DecisionFrameContext("frame-2", 2, 200, shared_memory={}), None)
        self.assertEqual(step.status()["available_plugins"], ["first", "second"])
        self.assertEqual(step.plugins, ())

    def test_manager_selection_runs_in_order_and_later_plugins_see_earlier_writes(self):
        manager = _manager()
        manager.select(["first", "second"])
        step = PluginMemoryRunner(plugin_manager=manager)
        shared = {}
        report = step.update(
            DecisionFrameContext("frame-1", 1, 100, shared_memory=shared),
            Observation("obs-1", 90, {}),
        )
        self.assertEqual(shared["execution_order"], ["first", "second"])
        self.assertEqual(shared["previous_outputs"], [None, "first"])
        self.assertEqual(shared["last_writer"], "second")
        self.assertEqual(
            [item["implementation_id"] for item in report["plugins"]], ["first", "second"]
        )
        self.assertEqual([item["update_count"] for item in step.status()["plugins"]], [1, 1])

    def test_reset_without_a_map_resets_in_the_last_map(self):
        manager = _manager()
        manager.select(["first"])
        step = PluginMemoryRunner(plugin_manager=manager)
        shared = {}
        step.update(DecisionFrameContext("frame-1", 1, 100, shared_memory=shared), Observation("obs-1", 90, {}))
        step.reset()
        self.assertEqual(shared["first.state"], {"epoch": 2, "records": ()})
        self.assertEqual(step.status()["plugins"][0]["state"]["epoch_id"], "epoch-2")

    def test_selection_changes_reuse_retained_plugins_and_reset_removed_plugins(self):
        manager = _manager()
        manager.select(["first", "second"])
        step = PluginMemoryRunner(plugin_manager=manager)
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
        step = PluginMemoryRunner(plugin_manager=manager)
        shared = {}
        context = DecisionFrameContext("frame-1", 1, 100, shared_memory=shared)
        self.assertEqual(step.update(context, None)["plugins"], [])
        manager.add("first")
        self.assertEqual(step.update(context, None)["plugins"][0]["implementation_id"], "first")
        shared["decision.observation"] = "published by the removed plugin"
        manager.remove("first")
        self.assertEqual(step.update(context, None)["plugins"], [])
        self.assertNotIn("decision.observation", shared)
        self.assertEqual(step.status()["plugin_ids"], [])

    def test_selection_id_stays_distinct_from_implementation_id(self):
        manager = memory_plugin_manager({"ledger": SPEC}, {"ledger": {}})
        manager.select(["ledger"])
        step = PluginMemoryRunner(plugin_manager=manager)
        self.assertEqual(step.plugin_ids, ("ledger",))
        self.assertEqual(step.plugins[0].implementation.implementation_id, "recording_test")
        plugin_status = step.status()["plugins"][0]
        self.assertEqual(plugin_status["plugin_id"], "ledger")
        self.assertEqual(plugin_status["implementation_id"], "recording_test")
        self.assertEqual(step.report()["plugins"][0]["implementation_id"], "recording_test")

    def test_legacy_activation_seeds_manager_and_explicit_selection_overrides_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = _valid_payload()
            activation = read_memory_activation(_write_payload(tmp, payload))
            self.assertEqual(PluginMemoryRunner(activation).plugin_ids, ("recording_test",))
            payload["memory"].update({
                "plugins": ["first", "second"],
                "plugin_specs": {"first": SPEC, "second": SPEC},
                "plugin_configs": {name: {"implementation_id": name} for name in ("first", "second")},
            })
            activation = read_memory_activation(_write_payload(tmp, payload))
            step = PluginMemoryRunner(activation)
            self.assertEqual(step.plugin_ids, ("first", "second"))
            self.assertEqual(set(step.reset({})), {"first.state", "second.state"})
            self.assertEqual([item["reset_count"] for item in step.status()["plugins"]], [1, 1])

    def test_prepare_selection_constructs_a_replacement_once(self):
        _OnceMemory.constructions = 0
        manager = memory_plugin_manager(
            {"first": SPEC, "second": f"{__name__}:_OnceMemory"},
            {
                "first": {"implementation_id": "first"},
                "second": {"implementation_id": "second"},
            },
        )
        manager.select(["first"])
        step = PluginMemoryRunner(plugin_manager=manager)
        original = step.plugins[0]
        manager.select(["second"])

        step.prepare_selection()

        self.assertEqual(_OnceMemory.constructions, 1)
        self.assertIs(step.plugins[0], original)
        self.assertEqual(original.reset_count, 0)
        step.commit_selection()
        self.assertEqual(_OnceMemory.constructions, 1)
        self.assertEqual(step.plugin_ids, ("second",))
        self.assertEqual(step.plugins[0].implementation.implementation_id, "second")
        self.assertEqual(original.reset_count, 1)

    def test_status_plugin_report_aliases_applied_execution(self) -> None:
        manager = memory_plugin_manager(
            {"first": SPEC, "second": SPEC},
            {
                "first": {"implementation_id": "first-impl", "fail_on_update": True},
                "second": {"implementation_id": "second-impl"},
            },
        )
        manager.select(["second", "first"])
        step = PluginMemoryRunner(plugin_manager=manager)

        with self.assertRaisesRegex(RuntimeError, "forced-update-failure"):
            step.update(DecisionFrameContext("frame-1", 1, 100, shared_memory={}), None)

        status = step.status()
        self.assertEqual(
            set(status),
            {
                "implementation_id",
                "implementation_spec",
                "activation",
                "available_plugins",
                "selected_plugin_ids",
                "plugin_ids",
                "plugins",
                "plugin_report",
                "update_count",
                "reset_count",
                "failure_count",
                "last_duration_ms",
                "last_error",
            },
        )
        self.assertEqual(status["plugin_ids"], ["second", "first"])
        self.assertEqual(status["selected_plugin_ids"], ["second", "first"])
        report = status["plugin_report"]
        self.assertEqual(report["available_plugin_ids"], ["first", "second"])
        self.assertEqual(report["selected_plugin_ids"], ["second", "first"])
        self.assertEqual(report["applied_plugin_ids"], ["second", "first"])
        self.assertEqual(
            [
                (
                    item["plugin_id"],
                    item["implementation_id"],
                    item["duration_ms"],
                    item["error"],
                )
                for item in report["plugins"]
            ],
            [
                (
                    item["plugin_id"],
                    item["implementation_id"],
                    item["last_duration_ms"],
                    item["last_error"],
                )
                for item in status["plugins"]
            ],
        )
        self.assertIsNotNone(report["plugins"][0]["duration_ms"])
        self.assertIsNone(report["plugins"][0]["error"])
        self.assertIn("forced-update-failure", report["plugins"][1]["error"])
        self.assertNotIn("bounds", report["plugins"][0])
        self.assertNotIn("last_health", report["plugins"][0])
        self.assertNotIn("status", report["plugins"][1])

    def test_plugin_report_keeps_applied_instances_until_selection_commits(self) -> None:
        manager = memory_plugin_manager(
            {
                "first": SPEC,
                "second": SPEC,
                "broken": f"{__name__}:_RejectMemory",
            },
            {"first": {"implementation_id": "first-impl"}},
        )
        manager.select(["first"])
        step = PluginMemoryRunner(plugin_manager=manager)
        manager.select(["second", "first"])

        pending = step.plugin_report()

        self.assertEqual(pending["selected_plugin_ids"], ["second", "first"])
        self.assertEqual(pending["applied_plugin_ids"], ["first"])
        self.assertEqual(
            pending["plugins"],
            [
                {
                    "plugin_id": "first",
                    "implementation_id": "first-impl",
                    "duration_ms": None,
                    "error": None,
                }
            ],
        )
        self.assertEqual(step.status()["plugin_ids"], ["first"])
        self.assertEqual(step.status()["selected_plugin_ids"], ["second", "first"])

        manager.select(["broken"])
        with self.assertRaisesRegex(RuntimeError, "rejected memory"):
            step.prepare_selection()

        rejected = step.status()
        report = rejected["plugin_report"]
        self.assertEqual(rejected["selected_plugin_ids"], ["broken"])
        self.assertEqual(rejected["plugin_ids"], ["first"])
        self.assertEqual(report["selected_plugin_ids"], ["broken"])
        self.assertEqual(report["applied_plugin_ids"], ["first"])
        self.assertEqual(report["available_plugin_ids"], ["broken", "first", "second"])
        self.assertEqual(report["plugins"][0]["plugin_id"], "first")
        self.assertEqual(report["plugins"][0]["implementation_id"], "first-impl")
        self.assertIsNone(report["plugins"][0]["duration_ms"])
        self.assertNotIn("bounds", report["plugins"][0])
