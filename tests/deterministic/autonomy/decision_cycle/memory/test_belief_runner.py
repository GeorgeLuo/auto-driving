from __future__ import annotations

import json
import unittest

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.memory.beliefs import Belief, Check
from autonomy.decision_cycle.memory.interface import MemoryReport
from autonomy.decision_cycle.memory.plugin import MemoryPluginContract
from autonomy.decision_cycle.memory.publication import BELIEF_KEY_PREFIX, belief_key, read_belief
from autonomy.decision_cycle.memory.runner import MemoryRunner


class _Learner:
    plugin_id = "learner"
    contract = MemoryPluginContract(description="Test checked beliefs.", belief_ids=("learner.row",))

    def __init__(self, belief_id="learner.row", *, fail_reset=False):
        self.belief_id = belief_id
        self.fail_reset = fail_reset

    def update(self, context, observation):
        belief = read_belief(context.shared_memory, self.belief_id)
        if belief is None:
            belief = Belief(self.belief_id, 12, self.plugin_id, context.timestamp_ms)
        context.shared_memory[belief_key(self.belief_id)] = belief.checked(
            Check(context.timestamp_ms, "stop", "support"), 8
        )

    def reset(self, shared_memory):
        if self.fail_reset:
            raise RuntimeError("reset failed")


def _context(shared, timestamp_ms=10):
    return DecisionFrameContext("f", 0, timestamp_ms, shared_memory=shared)


class BeliefRunnerTests(unittest.TestCase):
    def test_contract_is_required_even_when_update_exists(self):
        class MissingContract:
            plugin_id = "missing"
            def update(self, context, observation):
                pass
        with self.assertRaisesRegex(TypeError, "must expose MemoryPluginContract"):
            MemoryRunner.from_plugins({"missing": MissingContract()})
        plugin = _Learner()
        plugin.contract = {}
        with self.assertRaisesRegex(TypeError, "must expose MemoryPluginContract"):
            MemoryRunner.from_plugins({plugin.plugin_id: plugin})

    def test_contract_belief_ids_are_unique_identifiers(self):
        contract = MemoryPluginContract(belief_ids=["a.row"])
        self.assertEqual(contract.belief_ids, ("a.row",))
        self.assertEqual(contract.to_dict()["belief_ids"], ["a.row"])
        with self.assertRaises(TypeError):
            MemoryPluginContract(belief_ids="a.row")
        for belief_ids in (("",), (" ",), (1,), ("a.row", "a.row")):
            with self.subTest(belief_ids=belief_ids), self.assertRaises(ValueError):
                MemoryPluginContract(belief_ids=belief_ids)

    def test_a_plugin_without_belief_ids_publishes_none(self):
        class Counter:
            plugin_id = "counter"
            contract = MemoryPluginContract(description="Counts cycles; checks nothing.")
            def update(self, context, observation):
                context.shared_memory["memory.counter.count"] = context.shared_memory.get("memory.counter.count", 0) + 1
        shared = {}
        runner = MemoryRunner.from_plugins({"counter": Counter()})
        report = runner.update(_context(shared), None)
        self.assertEqual(report["beliefs"], {})
        self.assertEqual(runner.status()["beliefs"], {})
        self.assertIsNone(read_belief(shared, "counter.count"))

    def test_two_plugins_cannot_declare_the_same_belief(self):
        second = _Learner()
        second.plugin_id = "second"
        with self.assertRaisesRegex(ValueError, "'learner.row' is declared by both 'learner' and 'second'"):
            MemoryRunner.from_plugins({"learner": _Learner(), "second": second})

    def test_selected_plugins_publish_their_own_beliefs(self):
        second = _Learner("second.row")
        second.plugin_id = "second"
        second.contract = MemoryPluginContract(belief_ids=("second.row",))
        runner = MemoryRunner.from_plugins({"learner": _Learner(), "second": second})
        report = runner.update(_context({}), None)
        self.assertEqual(set(report["beliefs"]), {"learner.row", "second.row"})
        self.assertEqual(report["beliefs"]["second.row"]["source_plugin_id"], "second")

    def test_report_serializes_confidence(self):
        runner = MemoryRunner.from_plugins({"learner": _Learner()})
        shared = {}
        first = runner.update(_context(shared), None)
        second = runner.update(_context(shared, 20), None)
        self.assertAlmostEqual(first["beliefs"]["learner.row"]["confidence"], 2 / 3)
        self.assertAlmostEqual(second["beliefs"]["learner.row"]["confidence"], 3 / 4)
        self.assertEqual(MemoryReport.from_dict(json.loads(json.dumps(first))).to_dict(), first)
        self.assertAlmostEqual(runner.status()["beliefs"]["learner.row"]["confidence"], 3 / 4)

    def test_schema_names_the_belief_keys_and_each_plugins_contract(self):
        schema = MemoryRunner.from_plugins({"learner": _Learner()}).describe_schema()
        self.assertEqual(schema["composition"]["belief_key_prefix"], BELIEF_KEY_PREFIX)
        self.assertEqual(schema["composition"]["beliefs"], "one_declared_owner_per_belief")
        self.assertEqual([plugin["contract"] for plugin in schema["plugins"]], [_Learner.contract.to_dict()])

    def test_reset_and_deselection_remove_only_declared_beliefs_even_on_reset_failure(self):
        other = Belief("other.row", 15, "other", 0)
        for fail in (False, True):
            with self.subTest(fail=fail):
                runner = MemoryRunner.from_plugins({"learner": _Learner(fail_reset=fail)})
                shared = {belief_key("other.row"): other}
                runner.update(_context(shared), None)
                runner.reset()
                self.assertEqual(shared, {belief_key("other.row"): other})
                self.assertEqual(runner.failure_count, int(fail))
                runner.update(_context(shared, 20), None)
                runner.plugin_manager.select(())
                runner.apply_selection(shared)
                self.assertEqual(shared, {belief_key("other.row"): other})
