from __future__ import annotations

import json
import unittest
from dataclasses import FrozenInstanceError, replace

from autonomy.decision_cycle.memory.beliefs.values import Belief, Check, Trial
from autonomy.decision_cycle.memory.publication import BELIEFS_KEY, drop_beliefs, publish_belief, read_belief


def hypothesis(**changes):
    return Belief("test.row", {"median": 12, "q1": 11, "q3": 14}, "test", 0, **changes)


class BeliefTests(unittest.TestCase):
    def test_confidence_counts_recent_checks_not_samples(self):
        belief = hypothesis(properties={"samples": 1000})
        self.assertIsNone(belief.confidence)
        first = belief.checked(Check(1, "stop", "support"), max_checks=5)
        self.assertAlmostEqual(first.confidence, 2 / 3)
        belief = first
        for at_ms in range(2, 6):
            belief = belief.checked(Check(at_ms, "stop", "support"), max_checks=5)
        self.assertAlmostEqual(belief.confidence, 6 / 7)
        failed = belief.checked(Check(6, "stop", "contradict"), max_checks=5)
        self.assertAlmostEqual(failed.confidence, 5 / 7)
        self.assertEqual([check.at_ms for check in failed.checks], [2, 3, 4, 5, 6])
        self.assertEqual(belief.by_method, {"stop": {"support": 5, "contradict": 0}})
        self.assertIsNone(hypothesis().confidence)
        self.assertEqual(len(first.checks), 1)

    def test_corroboration_requires_two_methods_with_positive_balance(self):
        belief = hypothesis().checked(Check(1, "stop", "support"), 10)
        belief = belief.checked(Check(2, "stop", "support"), 10)
        self.assertFalse(belief.corroborated)
        belief = belief.checked(Check(3, "arrival", "support"), 10)
        self.assertTrue(belief.corroborated)
        belief = belief.checked(Check(4, "arrival", "contradict"), 10)
        self.assertFalse(belief.corroborated)
        self.assertFalse(belief.checked(Check(5, "stop", "support"), 1).corroborated)

    def test_resolving_a_trial_uses_its_prediction_and_method(self):
        initial = hypothesis()
        trial = Trial("stop-1", "stop", 1, {"row": 12}, {"frame": "f1"})
        opened = initial.opened(trial).opened(Trial("stop-2", "arrival", 2, 120))
        resolved = opened.resolved("stop-1", "support", {"row": 11}, 3, 8)
        self.assertEqual(resolved.checks, (Check(3, "stop", "support", {"row": 12}, {"row": 11}),))
        self.assertEqual([trial.trial_id for trial in resolved.trials], ["stop-2"])
        self.assertEqual(resolved.updated_at_ms, 3)
        self.assertEqual(initial.trials, ())
        self.assertEqual(len(opened.trials), 2)
        with self.assertRaisesRegex(ValueError, "unknown trial"):
            resolved.resolved("stop-1", "support", None, 4, 8)
        with self.assertRaisesRegex(ValueError, "before it starts"):
            opened.resolved("stop-2", "support", None, 1, 8)
        with self.assertRaisesRegex(ValueError, "unique"):
            opened.opened(trial)

    def test_revision_can_retire_old_checks_and_retires_open_predictions(self):
        belief = hypothesis().checked(Check(1, "stop", "support"), 5)
        belief = belief.opened(Trial("t", "stop", 2, 12))
        retained = belief.revised(13, 3, keep_checks=True)
        self.assertEqual(retained.checks, belief.checks)
        self.assertEqual(retained.trials, ())
        discarded = belief.revised(14, 4, keep_checks=False)
        self.assertIsNone(discarded.confidence)
        self.assertEqual((discarded.value, discarded.updated_at_ms), (14, 4))

    def test_json_round_trip_and_serialization_detach_nested_data(self):
        raw = {"range": [1, 2]}
        belief = Belief("test", raw, "test", 0, properties=raw)
        raw["range"][0] = 99
        self.assertEqual(belief.value["range"], [1, 2])
        belief = belief.opened(Trial("t", "arrival", 1, raw, raw))
        belief = belief.checked(Check(2, "stop", "support", raw, raw), 8)
        encoded = json.loads(json.dumps(belief.to_dict()))
        self.assertEqual(Belief.from_dict(encoded), belief)
        encoded["value"]["range"][0] = 33
        encoded["checks"][0]["predicted"]["range"][0] = 44
        self.assertEqual(belief.value["range"], [1, 2])
        self.assertEqual(belief.checks[0].predicted["range"][0], 99)
        for value, attribute in ((belief, "value"), (belief.checks[0], "method"), (belief.trials[0], "method")):
            with self.assertRaises(FrozenInstanceError):
                setattr(value, attribute, None)

    def test_invalid_values_and_bounds_are_rejected(self):
        factories = (
            lambda: Check(-1, "stop", "support"),
            lambda: Check(True, "stop", "support"),
            lambda: Check(1, "", "support"),
            lambda: Check(1, "stop", "maybe"),
            lambda: Trial("", "stop", 1, 12),
            lambda: Trial("t", "stop", -1, 12),
            lambda: Trial("t", "stop", 1, float("nan")),
            lambda: Belief("", 1, "test", 0),
            lambda: Belief("test", 1, "", 0),
            lambda: Belief("test", 1, "test", -1),
            lambda: hypothesis(properties=[]),
            lambda: hypothesis().revised(object(), 1, False),
            lambda: hypothesis(checks=("support",)),
            lambda: hypothesis(trials=("trial",)),
            lambda: Belief.from_dict({**hypothesis().to_dict(), "updated_at_ms": -1}),
        )
        for factory in factories:
            with self.subTest(factory=factory), self.assertRaises((ValueError, TypeError)):
                factory()
        for bound in (0, -1, True, 1.5):
            with self.subTest(bound=bound), self.assertRaises(ValueError):
                hypothesis().checked(Check(1, "stop", "support"), bound)

    def test_publication_merges_by_id_and_readers_are_detached(self):
        shared = {"unrelated": 3}
        first = hypothesis().checked(Check(1, "stop", "support"), 5)
        second = replace(first, belief_id="test.speed")
        publish_belief(shared, first)
        publish_belief(shared, second)
        self.assertEqual(set(shared[BELIEFS_KEY]), {first.belief_id, second.belief_id})
        read = read_belief(shared, first.belief_id)
        self.assertEqual(read, first)
        read.value["median"] = 99
        self.assertEqual(read_belief(shared, first.belief_id).value["median"], 12)
        self.assertIsNone(read_belief(shared, "absent"))
        self.assertIsNone(read_belief(None, "absent"))
        drop_beliefs(shared, (first.belief_id,))
        self.assertEqual(set(shared[BELIEFS_KEY]), {second.belief_id})
        self.assertEqual(shared["unrelated"], 3)
