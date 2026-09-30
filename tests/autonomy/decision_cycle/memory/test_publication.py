from __future__ import annotations

import unittest
from dataclasses import replace

from autonomy.decision_cycle.memory.publication import (
    OBSERVATION_KEY,
    observation_after_memory,
    withdraw_publication,
)
from autonomy.decision_cycle.observation.values import Observation


class MemoryPublicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.observation = Observation("frame-1", 100, {})
        self.replacement = replace(self.observation, summary=("updated",))

    def test_key(self) -> None:
        self.assertEqual(OBSERVATION_KEY, "decision.observation")

    def test_withdraw_publication(self) -> None:
        shared = {OBSERVATION_KEY: self.replacement, "other": 1}
        withdraw_publication(shared)
        self.assertEqual(shared, {"other": 1})

    def test_replacement_applies_to_the_same_observation(self) -> None:
        shared = {OBSERVATION_KEY: self.replacement}
        self.assertIs(observation_after_memory(shared, self.observation), self.replacement)

    def test_replacement_is_ignored_otherwise(self) -> None:
        other = Observation("frame-2", 100, {})
        cases = (
            (None, self.observation),
            ({OBSERVATION_KEY: {"observation_id": "frame-1"}}, self.observation),
            ({OBSERVATION_KEY: other}, self.observation),
            ({OBSERVATION_KEY: self.replacement}, None),
        )
        for shared, observation in cases:
            with self.subTest(shared=shared, observation=observation):
                self.assertIs(observation_after_memory(shared, observation), observation)


if __name__ == "__main__":
    unittest.main()
