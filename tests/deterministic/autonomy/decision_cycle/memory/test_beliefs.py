from __future__ import annotations

import json
import unittest
from dataclasses import FrozenInstanceError

from autonomy.decision_cycle.memory.beliefs import Belief, Check, Trial
from autonomy.decision_cycle.memory.publication import belief_key, read_belief
from autonomy.shared_memory import shared_memory_document, shared_memory_from_document


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

    def test_an_unchecked_belief_serializes_null_confidence(self):
        data = json.loads(json.dumps(hypothesis().to_dict()))
        self.assertIsNone(data["confidence"])
        self.assertEqual((data["by_method"], data["corroborated"]), ({}, False))

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
        opened = initial.opened(trial, 4).opened(Trial("stop-2", "arrival", 2, 120), 4)
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

    def test_open_trials_are_bounded_and_time_does_not_move_backwards(self):
        belief = Belief("test.row", 12, "test", 10)
        for index in range(4):
            belief = belief.opened(Trial(f"t{index}", "stop", index, index), max_trials=2)
        self.assertEqual([trial.trial_id for trial in belief.trials], ["t2", "t3"])
        self.assertEqual(belief.updated_at_ms, 10)
        self.assertEqual(belief.checked(Check(5, "stop", "support"), 8).updated_at_ms, 10)
        self.assertEqual(belief.revised(13, 4, keep_checks=True).updated_at_ms, 10)
        self.assertEqual(belief.checked(Check(15, "stop", "support"), 8).updated_at_ms, 15)
        for bound in (0, -1, True, 1.5):
            with self.subTest(bound=bound), self.assertRaises(ValueError):
                belief.opened(Trial("t9", "stop", 1, 1), bound)

    def test_revision_can_retire_old_checks_and_keeps_open_predictions(self):
        belief = hypothesis().checked(Check(1, "stop", "support"), 5)
        belief = belief.opened(Trial("t", "stop", 2, 12), 4)
        retained = belief.revised(13, 3, keep_checks=True)
        self.assertEqual(retained.checks, belief.checks)
        self.assertEqual(retained.trials, belief.trials)
        discarded = belief.revised(14, 4, keep_checks=False)
        self.assertIsNone(discarded.confidence)
        self.assertEqual((discarded.value, discarded.updated_at_ms), (14, 4))

    def test_json_round_trip_derives_confidence_and_records_are_frozen(self):
        belief = Belief("test", {"range": [1, 2]}, "test", 0, properties={"samples": 3})
        belief = belief.opened(Trial("t", "arrival", 1, {"row": 12}, {"frame": "f1"}), 4)
        belief = belief.checked(Check(2, "stop", "support", {"row": 12}, {"row": 11}), 8)
        encoded = json.loads(json.dumps(belief.to_dict()))
        self.assertEqual(Belief.from_dict(encoded), belief)
        forged = {**encoded, "confidence": 1.0, "by_method": {}, "corroborated": True}
        self.assertEqual(Belief.from_dict(forged).to_dict(), belief.to_dict())
        for value, attribute in ((belief, "value"), (belief.checks[0], "method"), (belief.trials[0], "method")):
            with self.assertRaises(FrozenInstanceError):
                setattr(value, attribute, None)

    def test_invalid_values_and_bounds_are_rejected(self):
        factories = (
            lambda: Check(-1, "stop", "support"),
            lambda: Check("soon", "stop", "support"),
            lambda: Check(1, "", "support"),
            lambda: Check(1, "stop", "maybe"),
            lambda: Trial("", "stop", 1, 12),
            lambda: Trial("t", "stop", -1, 12),
            lambda: Belief("", 1, "test", 0),
            lambda: Belief("test", 1, "", 0),
            lambda: Belief("test", 1, "test", -1),
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

    def test_a_belief_is_stored_in_shared_memory_as_a_record(self):
        belief = hypothesis().opened(Trial("t", "stop", 1, {"row": 12}), 4)
        belief = belief.checked(Check(2, "stop", "support"), 8)
        document = json.loads(json.dumps(shared_memory_document({belief_key(belief.belief_id): belief})))
        self.assertEqual(shared_memory_from_document(document), {"memory.beliefs.test.row": belief})

    def test_readers_get_the_stored_belief_or_none(self):
        belief = hypothesis().checked(Check(1, "stop", "support"), 5)
        shared = {belief_key(belief.belief_id): belief, belief_key("test.raw"): belief.to_dict()}
        self.assertIs(read_belief(shared, belief.belief_id), belief)
        for shared_memory, belief_id in ((shared, "absent"), (shared, "test.raw"), (None, "test.row")):
            with self.subTest(belief_id=belief_id):
                self.assertIsNone(read_belief(shared_memory, belief_id))

    def test_revision_properties_and_default_check_retention(self):
        belief = hypothesis(properties={"samples": 2}).checked(Check(10, "stop", "support"), 5)
        revised = belief.revised(14, 3)
        self.assertEqual(revised.properties, {"samples": 2})
        self.assertEqual(revised.checks, belief.checks)
        self.assertEqual(revised.updated_at_ms, 10)
        self.assertEqual(belief.revised(14, 11, properties={}).properties, {})

    def test_opening_replaces_same_id_and_preserves_other_trials(self):
        first = Trial("first", "stop", 10, 12)
        second = Trial("second", "arrival", 12, 15)
        belief = hypothesis().opened(first, 2).opened(second, 2)
        updated = Trial("first", "stop", 8, 14)
        replaced = belief.opened(updated, 2)
        self.assertEqual(replaced.trials, (second, updated))
        self.assertEqual(replaced.updated_at_ms, 12)
        self.assertEqual(replaced.opened(Trial("third", "stop", 15, 16), 2).trials[0], updated)

    def test_withdrawal_drops_predictions_without_scoring(self):
        belief = hypothesis().opened(Trial("first", "stop", 10, 12), 3)
        belief = belief.opened(Trial("second", "arrival", 12, 15), 3)
        belief = belief.checked(Check(13, "stop", "support"), 5)
        withdrawn = belief.withdrawn(("first", "missing"))
        self.assertEqual(withdrawn.trials, (belief.trials[1],))
        self.assertEqual(withdrawn.checks, belief.checks)
        self.assertEqual(withdrawn.updated_at_ms, 13)
        self.assertEqual(belief.withdrawn(()), belief)
        self.assertEqual(belief.withdrawn().trials, ())
        self.assertEqual(belief.withdrawn().confidence, belief.confidence)

    def test_summary_omits_retained_records_and_properties(self):
        belief = hypothesis(properties={"samples": 2}).opened(Trial("t", "stop", 10, 12), 3)
        self.assertEqual(belief.summary(), {
            "value": belief.value, "source_plugin_id": "test", "updated_at_ms": 10,
            "confidence": None, "by_method": {}, "corroborated": False, "open_trials": 1,
        })
