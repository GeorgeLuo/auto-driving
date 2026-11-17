"""A change invalidates the executable suffix, independent of the caller."""
from dataclasses import replace
from types import SimpleNamespace
import unittest

from autonomy.decision_cycle.activation import STEPS, step_activation
from autonomy.decision_cycle.restage import restage


def activation(step, *plugins):
    return step_activation(step, plugins, {plugin: f"test:{plugin}" for plugin in plugins})


class RestageTests(unittest.TestCase):
    def test_only_the_changed_step_rebuilds_and_only_the_affected_suffix_loses_state(self):
        applied = {step: activation(step, step) for step in STEPS}
        applied["perception"] = activation("perception", "first", "middle", "last")
        applied["memory"] = activation("memory", "learner", "other")
        steps = {step: SimpleNamespace(applied=()) for step in STEPS}
        steps["memory"].applied = (
            (SimpleNamespace(plugin_id="learner"), SimpleNamespace(contract=SimpleNamespace(belief_ids=("learned",)))),
        )
        keys = [
            "perception.first.history", "perception.middle.history", "perception.last.history",
            "observation.observation.history", "observation.current",
            "memory.learner.ledger", "memory.beliefs.learned", "memory.other.ledger", "memory.evidence",
            "proposal.proposal.history", "plan.plan.history", "action.action.history",
            "action.mode", "action.user_steering", "action.user_throttle", "action.control_applications", "seed",
        ]
        shared = {key: object() for key in keys}
        cases = (
            ("proposal change", "proposal", activation("proposal", "new"), keys[:9] + keys[12:]),
            ("plan change", "plan", activation("plan", "new"), keys[:10] + keys[12:]),
            ("perception append", "perception", activation("perception", "first", "middle", "last", "new"), keys[:3] + keys[12:]),
            ("middle config", "perception", replace(applied["perception"], plugin_configs={"middle": {"gain": 2}}), keys[:1] + keys[12:]),
            ("middle revision", "perception", replace(applied["perception"], metadata={"plugin_revisions": {"middle": "new"}}), keys[:1] + keys[12:]),
            ("reorder", "perception", activation("perception", "first", "last", "middle"), keys[:1] + keys[12:]),
            ("remove beliefs owner", "memory", activation("memory", "other"), keys[:5] + keys[12:]),
            ("source identity", "perception", replace(applied["perception"], metadata={"host_name": "other"}), keys[12:]),
            ("display only", "plan", replace(applied["plan"], metadata={"preset": "renamed"}), keys),
        )
        for name, step, desired, kept in cases:
            with self.subTest(name=name):
                built = []

                def build(name, selection):
                    built.append((name, selection))
                    return object()

                changed = restage(steps, applied, {**applied, step: desired}, build=build)
                if name == "display only":
                    self.assertIsNone(changed)
                    self.assertEqual(built, [])
                    continue
                self.assertEqual(built, [(step, desired)])
                self.assertEqual(set(changed.keep(shared)), set(kept))
                for key, value in changed.keep(shared).items():
                    self.assertIs(value, shared[key])
                for other in STEPS:
                    self.assertEqual(changed.steps[other] is steps[other], other != step)
                self.assertEqual(set(shared), set(keys))

    def test_a_steps_publication_is_cleared_with_any_of_its_plugins(self):
        applied = {step: activation(step, step) for step in STEPS}
        steps = {step: SimpleNamespace(applied=()) for step in STEPS}
        shared = {"proposal.policy": object(), "proposal.proposal.policy": object()}

        def build(name, selection):
            return object()

        proposal = restage(steps, applied, {**applied, "proposal": activation("proposal", "new")}, build=build)
        plan = restage(steps, applied, {**applied, "plan": activation("plan", "new")}, build=build)
        self.assertEqual(proposal.keep(shared), {})
        self.assertEqual(plan.keep(shared), shared)
