"""Plugin calls enforce map ownership under each step's failure policy."""
import unittest

from autonomy.decision_cycle.activation import STEPS
from autonomy.decision_cycle.action.hold import HoldAction
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.memory.plugin import MemoryPluginContract
from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.perception.evidence.values import PerceptionEvidenceBatch
from autonomy.decision_cycle.perception.feeds.context import PerceptionRequest
from autonomy.decision_cycle.perception.plugin import PerceptionPluginContract
from autonomy.decision_cycle.plugin_state import RetainedStateError
from autonomy.decision_cycle.steps import STEP_RUNNERS
from autonomy.vehicle import SensorFrame
from tests.support.action_fixtures import InactiveProposal, FirstActivePlan


class Writer:
    plugin_id = "writer"

    def __init__(self, step, key, operation="add"):
        self.key, self.operation = key, operation
        self.contract = MemoryPluginContract(belief_ids=("declared",)) if step == "memory" else PerceptionPluginContract()

    def write(self, shared):
        if self.operation == "remove":
            shared.pop(self.key)
        else:
            shared[self.key] = object()

    def perceive(self, inputs):
        self.write(inputs.shared_memory)
        return PerceptionEvidenceBatch()

    def observe(self, context, perception):
        self.write(context.shared_memory)
        return Observation("obs", 100, {})

    def update(self, context):
        self.write(context.shared_memory)

    def propose(self, context):
        self.write(context.shared_memory)
        return InactiveProposal.propose(self, context)

    def plan(self, context, candidates):
        self.write(context.shared_memory)
        return FirstActivePlan.plan(self, context, candidates)

    def decide(self, context, plan, **kwargs):
        self.write(context.shared_memory)
        return HoldAction().decide(context, plan, **kwargs)

    def reset(self, shared_memory):
        self.write(shared_memory)


def invoke(step, runner, shared):
    context = DecisionFrameContext("f", 0, 100, shared_memory=shared)
    if step == "perception":
        return runner.perceive(PerceptionRequest(SensorFrame("f", {}, 100, 100), shared_memory=shared))
    if step in {"observation", "plan"}:
        return runner(context, None)
    if step == "action":
        return runner(context, None, None)
    return runner(context)


class OwnershipTests(unittest.TestCase):
    def test_each_steps_updates_accept_its_namespace_and_reject_foreign_add_replace_and_remove(self):
        for step in STEPS:
            for operation in ("add", "replace", "remove"):
                for owned in (False, True):
                    key = f"{step}.{'writer' if owned else 'other'}.history"
                    with self.subTest(step=step, operation=operation, owned=owned):
                        runner = STEP_RUNNERS[step].from_plugins({"writer": Writer(step, key, operation)})
                        shared = {} if operation == "add" else {key: object()}
                        if not owned and step in {"memory", "observation"}:
                            with self.assertRaises(RetainedStateError):
                                invoke(step, runner, shared)
                        else:
                            invoke(step, runner, shared)
                            record = runner.plugin_report()["plugins"][0]
                            self.assertEqual(record["error"] is None, owned)
                            if not owned:
                                self.assertIn("RetainedStateError", record["error"])

    def test_declared_beliefs_and_evidence_are_writable_but_undeclared_beliefs_host_inputs_and_seeds_are_not(self):
        for key, allowed in (("memory.evidence", True), ("memory.beliefs.declared", True),
                             ("memory.beliefs.undeclared", False), ("action.mode", False), ("seed", False)):
            with self.subTest(key=key):
                runner = STEP_RUNNERS["memory"].from_plugins({"writer": Writer("memory", key)})
                if allowed:
                    invoke("memory", runner, {})
                else:
                    with self.assertRaises(RetainedStateError):
                        invoke("memory", runner, {})

    def test_a_plugin_may_publish_for_its_own_step_but_not_another_or_the_host(self):
        for step in STEPS:
            other = STEPS[(STEPS.index(step) + 1) % len(STEPS)]
            for key, allowed in ((f"{step}.published", True), (f"{other}.published", False), ("action.mode", False)):
                with self.subTest(step=step, key=key):
                    runner = STEP_RUNNERS[step].from_plugins({"writer": Writer(step, key)})
                    if not allowed and step in {"memory", "observation"}:
                        with self.assertRaises(RetainedStateError):
                            invoke(step, runner, {})
                        continue
                    invoke(step, runner, {})
                    record = runner.plugin_report()["plugins"][0]
                    self.assertEqual(record["error"] is None, allowed, record["error"])

    def test_resets_use_the_same_boundary_and_preserve_each_steps_failure_policy(self):
        for step in STEPS:
            for owned in (True, False):
                key = f"{step}.{'writer' if owned else 'other'}.history"
                with self.subTest(step=step, owned=owned):
                    runner = STEP_RUNNERS[step].from_plugins({"writer": Writer(step, key)})
                    if not owned and step != "memory":
                        with self.assertRaises(RetainedStateError):
                            runner.reset({})
                    else:
                        runner.reset({})
                        if not owned:
                            self.assertIn("RetainedStateError", runner.status()["plugins"][0]["last_error"])
