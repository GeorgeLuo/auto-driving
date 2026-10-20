from __future__ import annotations

import json
import unittest
from dataclasses import replace
from unittest.mock import Mock

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.cycle import DecisionCycle, DecisionSteps
from autonomy.decision_cycle.memory.beliefs.values import Belief, Check
from autonomy.decision_cycle.memory.errors import MemoryUpdateError
from autonomy.decision_cycle.memory.interface import MemoryReport
from autonomy.decision_cycle.memory.plugin import MemoryPluginContract
from autonomy.decision_cycle.memory.publication import BELIEFS_KEY, publish_belief, read_belief
from autonomy.decision_cycle.memory.runner import MemoryRunner


class _Learner:
    plugin_id = "learner"
    contract = MemoryPluginContract(description="Test checked beliefs.", emits=("learner.row",))

    def __init__(self, belief_id="learner.row", *, fail_reset=False):
        self.belief_id = belief_id
        self.fail_reset = fail_reset

    def update(self, context, observation):
        belief = read_belief(context.shared_memory, self.belief_id)
        if belief is None:
            belief = Belief(self.belief_id, 12, self.plugin_id, context.timestamp_ms)
        publish_belief(context.shared_memory, belief.checked(Check(context.timestamp_ms, "stop", "support"), 8))

    def reset(self, shared_memory):
        if self.fail_reset:
            raise RuntimeError("reset failed")


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

    def test_undeclared_belief_stops_the_cycle_and_records_the_plugin_error(self):
        plugin = _Learner("undeclared")
        runner = MemoryRunner.from_plugins({plugin.plugin_id: plugin})
        later_step = Mock()
        cycle = DecisionCycle(DecisionSteps(memory=runner, proposal=later_step))
        with self.assertRaisesRegex(MemoryUpdateError, "undeclared belief"):
            cycle.run(DecisionFrameContext("f", 0, 10, shared_memory={}))
        later_step.assert_not_called()
        self.assertEqual(runner.failure_count, 1)
        self.assertEqual(runner.plugins[plugin.plugin_id].failure_count, 1)
        self.assertIn("undeclared belief", runner.status()["plugins"][0]["last_error"])

    def test_in_place_undeclared_changes_are_detected(self):
        class Mutator(_Learner):
            contract = MemoryPluginContract()
            def update(self, context, observation):
                context.shared_memory[BELIEFS_KEY]["other.row"]["value"] = 99
        shared = {}
        publish_belief(shared, Belief("other.row", 12, "other", 0))
        runner = MemoryRunner.from_plugins({"learner": Mutator()})
        with self.assertRaisesRegex(ValueError, "undeclared belief"):
            runner.update(DecisionFrameContext("f", 0, 10, shared_memory=shared), None)

    def test_selected_learners_merge_beliefs_and_keep_source_attribution(self):
        first = _Learner()
        second = _Learner("second.row")
        second.plugin_id = "second"
        second.contract = MemoryPluginContract(emits=("second.row",))
        runner = MemoryRunner.from_plugins({"learner": first, "second": second})
        shared = {}
        report = runner.update(DecisionFrameContext("f", 0, 10, shared_memory=shared), None)
        self.assertEqual(set(report["beliefs"]), {"learner.row", "second.row"})
        self.assertEqual(report["beliefs"]["second.row"]["source_plugin_id"], "second")

    def test_a_declared_belief_must_identify_its_publisher(self):
        class WrongSource(_Learner):
            def update(self, context, observation):
                publish_belief(context.shared_memory, Belief("learner.row", 12, "other", 10))
        runner = MemoryRunner.from_plugins({"learner": WrongSource()})
        with self.assertRaisesRegex(ValueError, "must identify its publishing plugin"):
            runner.update(DecisionFrameContext("f", 0, 10, shared_memory={}), None)

    def test_report_serializes_confidence_and_keeps_earlier_reports_detached(self):
        runner = MemoryRunner.from_plugins({"learner": _Learner()})
        shared = {}
        context = DecisionFrameContext("f", 0, 10, shared_memory=shared)
        first = runner.update(context, None)
        second = runner.update(replace(context, timestamp_ms=20), None)
        self.assertAlmostEqual(first["beliefs"]["learner.row"]["confidence"], 2 / 3)
        self.assertAlmostEqual(second["beliefs"]["learner.row"]["confidence"], 3 / 4)
        self.assertEqual(MemoryReport.from_dict(json.loads(json.dumps(first))).to_dict(), first)
        second["beliefs"]["learner.row"]["value"] = 99
        self.assertEqual(read_belief(shared, "learner.row").value, 12)
        self.assertAlmostEqual(runner.status()["beliefs"]["learner.row"]["confidence"], 3 / 4)
        composition = runner.describe_schema()["composition"]
        self.assertEqual(composition["beliefs_key"], BELIEFS_KEY)
        self.assertEqual(composition["beliefs"], "merge_by_belief_id_last_write_wins")

    def test_reset_and_deselection_drop_only_declared_beliefs_even_on_reset_failure(self):
        for fail in (False, True):
            with self.subTest(fail=fail):
                runner = MemoryRunner.from_plugins({"learner": _Learner(fail_reset=fail)})
                shared = {}
                runner.update(DecisionFrameContext("f", 0, 10, shared_memory=shared), None)
                publish_belief(shared, Belief("other.row", 15, "other", 0))
                runner.reset()
                self.assertIsNone(read_belief(shared, "learner.row"))
                self.assertEqual(read_belief(shared, "other.row").value, 15)
                self.assertEqual(runner.failure_count, int(fail))
                runner.update(DecisionFrameContext("f2", 1, 20, shared_memory=shared), None)
                runner.plugin_manager.select(())
                runner.apply_selection(shared)
                self.assertIsNone(read_belief(shared, "learner.row"))
                self.assertEqual(read_belief(shared, "other.row").value, 15)
