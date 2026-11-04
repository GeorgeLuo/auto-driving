from __future__ import annotations

import json
import unittest
from dataclasses import replace

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.steps import decision_steps
from autonomy.decision_cycle.proposal.runner import ProposalRunner
from autonomy.runtime.report import report_from_host_result
from implementations.runtime.picar.control import DonkeyControlTarget
from autonomy.runtime.cycle_host import AutonomyCycleHost
from autonomy.decision_cycle.memory.beliefs import Belief, Check
from autonomy.decision_cycle.memory.interface import MemoryReport
from autonomy.decision_cycle.memory.plugin import MemoryPluginContract
from autonomy.decision_cycle.memory.publication import belief_key, read_belief
from autonomy.decision_cycle.memory.runner import MemoryRunner


class _Learner:
    plugin_id = "learner"
    contract = MemoryPluginContract(description="Test checked beliefs.", belief_ids=("learner.row",))

    def __init__(self, belief_id="learner.row", *, fail_reset=False):
        self.belief_id = belief_id
        self.fail_reset = fail_reset

    def update(self, context):
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
    def test_non_json_status_publishes_a_visible_error_on_its_plugin(self):
        class BadStatus(_Learner):
            def status(self, shared_memory):
                return {"bad": {1, 2}}

        host = AutonomyCycleHost(
            steps=replace(
                decision_steps(), proposal=ProposalRunner.from_plugins({}),
                memory=MemoryRunner.from_plugins({"learner": BadStatus()}),
            ),
            target=DonkeyControlTarget(),
        )
        self.addCleanup(host.close)
        result = host.run(_context({}))
        report = report_from_host_result(result, host_name="test", run_id="run", generation_id="gen").to_dict()
        state = report["cycle"]["memory"]["plugins"][0]["state"]
        self.assertIn("status_error", state)
        self.assertIn("JSON", state["status_error"])

    def test_non_json_belief_fails_in_the_plugin_update_and_stops_the_cycle(self):
        class BadBelief(_Learner):
            def update(self, context):
                Belief(self.belief_id, {1, 2}, self.plugin_id, context.timestamp_ms)

        runner = MemoryRunner.from_plugins({"learner": BadBelief()})
        with self.assertRaisesRegex(ValueError, "JSON"):
            runner.update(_context({}))
        self.assertEqual(runner.failure_count, 1)
        self.assertIn("JSON", runner.last_error)

    def test_contract_is_required_even_when_update_exists(self):
        class MissingContract:
            plugin_id = "missing"
            def update(self, context):
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
            def update(self, context):
                context.shared_memory["memory.counter.count"] = context.shared_memory.get("memory.counter.count", 0) + 1
        shared = {}
        runner = MemoryRunner.from_plugins({"counter": Counter()})
        report = runner.update(_context(shared))
        self.assertEqual(report["beliefs"], {})
        self.assertEqual(runner.status()["beliefs"], {})
        self.assertIsNone(read_belief(shared, "counter.count"))

    def test_two_plugins_can_declare_the_same_belief_and_last_write_wins(self):
        class Second(_Learner):
            plugin_id = "second"

            def update(self, context):
                context.shared_memory[belief_key(self.belief_id)] = Belief(
                    self.belief_id, 99, self.plugin_id, context.timestamp_ms
                )

        shared = {}
        runner = MemoryRunner.from_plugins({"learner": _Learner(), "second": Second()})
        report = runner.update(_context(shared))
        self.assertEqual(report["beliefs"]["learner.row"]["value"], 99)
        self.assertEqual(read_belief(shared, "learner.row").source_plugin_id, "second")

    def test_selected_plugins_publish_their_own_beliefs(self):
        second = _Learner("second.row")
        second.plugin_id = "second"
        second.contract = MemoryPluginContract(belief_ids=("second.row",))
        runner = MemoryRunner.from_plugins({"learner": _Learner(), "second": second})
        report = runner.update(_context({}))
        self.assertEqual(set(report["beliefs"]), {"learner.row", "second.row"})
        self.assertEqual(report["beliefs"]["second.row"]["source_plugin_id"], "second")

    def test_report_serializes_confidence(self):
        runner = MemoryRunner.from_plugins({"learner": _Learner()})
        shared = {}
        first = runner.update(_context(shared))
        second = runner.update(_context(shared, 20))
        self.assertAlmostEqual(first["beliefs"]["learner.row"]["confidence"], 2 / 3)
        self.assertAlmostEqual(second["beliefs"]["learner.row"]["confidence"], 3 / 4)
        decoded = MemoryReport.from_dict(json.loads(json.dumps(first)))
        self.assertEqual(decoded.to_dict(), first)
        self.assertEqual(set(first["beliefs"]["learner.row"]), {
            "value", "source_plugin_id", "updated_at_ms", "confidence", "by_method",
            "corroborated", "open_trials",
        })
        self.assertIn("checks", runner.status()["beliefs"]["learner.row"])
        self.assertIsNot(MemoryReport.from_dict(first).beliefs["learner.row"], first["beliefs"]["learner.row"])
        self.assertIsNot(decoded.to_dict()["beliefs"]["learner.row"], decoded.beliefs["learner.row"])
        self.assertAlmostEqual(runner.status()["beliefs"]["learner.row"]["confidence"], 3 / 4)

    def test_reset_and_deselection_remove_only_declared_beliefs_even_on_reset_failure(self):
        other = Belief("other.row", 15, "other", 0)
        for fail in (False, True):
            with self.subTest(fail=fail):
                runner = MemoryRunner.from_plugins({"learner": _Learner(fail_reset=fail)})
                shared = {belief_key("other.row"): other}
                runner.update(_context(shared))
                runner.reset()
                self.assertEqual(shared, {belief_key("other.row"): other})
                self.assertEqual(runner.failure_count, int(fail))
                runner.update(_context(shared, 20))
                runner.plugin_manager.select(())
                runner.apply_selection(shared)
                self.assertEqual(shared, {belief_key("other.row"): other})

    def test_report_keys_follow_publication_keys_not_embedded_ids(self):
        belief = Belief("same", 12, "learner", 0)
        shared = {belief_key("a"): belief, belief_key("b"): belief}
        runner = MemoryRunner.from_plugins({})
        report = runner.update(_context(shared))
        self.assertEqual(set(report["beliefs"]), {"a", "b"})
        self.assertEqual(set(runner.status()["beliefs"]), {"a", "b"})

    def test_reset_preserves_fresh_beliefs_and_advances_the_epoch(self):
        class Prior(_Learner):
            def reset(self, shared_memory):
                assert belief_key(self.belief_id) not in shared_memory
                epoch = shared_memory.get("memory.learner.epoch", 0) + 1
                shared_memory["memory.learner.epoch"] = epoch
                shared_memory[belief_key(self.belief_id)] = Belief(self.belief_id, epoch, self.plugin_id, 0)

        runner = MemoryRunner.from_plugins({"learner": Prior()})
        shared = {"memory.learner.epoch": 3}
        runner.update(_context(shared))
        fresh = runner.reset(shared)
        self.assertEqual(fresh[belief_key("learner.row")].value, 4)
        fresh = runner.reset(shared)
        self.assertEqual(fresh[belief_key("learner.row")].value, 5)
        self.assertEqual(shared[belief_key("learner.row")].value, 5)
        self.assertEqual(shared["memory.learner.epoch"], 5)
