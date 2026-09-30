from __future__ import annotations

import unittest
from dataclasses import replace

from autonomy.decision_cycle.memory.publication import (
    OBSERVATION_KEY,
    SNAPSHOT_KEY,
    observation_after_memory,
    publish_snapshot,
    withdraw_publication,
)
from autonomy.decision_cycle.memory.snapshots.values import (
    MemoryBounds,
    empty_memory_snapshot,
    error_memory_snapshot,
)
from autonomy.decision_cycle.observation.values import Observation


class MemoryPublicationTests(unittest.TestCase):
    def setUp(self) -> None:
        bounds = MemoryBounds(max_records=4)
        self.snapshot = empty_memory_snapshot(
            memory_id="memory-1", epoch_id="epoch-1", bounds=bounds, created_at_ms=100,
        )
        self.error = error_memory_snapshot(
            memory_id="memory-2", epoch_id="epoch-2", bounds=bounds, created_at_ms=100, error="failed",
        )
        self.observation = Observation("frame-1", 100, {})
        self.replacement = replace(self.observation, summary=("updated",))

    def test_keys(self) -> None:
        self.assertEqual(SNAPSHOT_KEY, "decision.snapshot")
        self.assertEqual(OBSERVATION_KEY, "decision.observation")

    def test_publish_snapshot_stores_none(self) -> None:
        shared = {}
        publish_snapshot(shared, None)
        self.assertEqual(shared, {SNAPSHOT_KEY: None})

    def test_withdraw_publication(self) -> None:
        shared = {SNAPSHOT_KEY: self.snapshot, OBSERVATION_KEY: self.replacement, "other": 1}
        withdraw_publication(shared)
        self.assertEqual(shared, {"other": 1})

    def test_replacement_applies_to_same_observation_after_healthy_memory(self) -> None:
        shared = {OBSERVATION_KEY: self.replacement}
        self.assertIs(observation_after_memory(shared, self.observation, self.snapshot), self.replacement)

    def test_replacement_is_ignored_otherwise(self) -> None:
        other = Observation("frame-2", 100, {})
        cases = (
            (None, self.observation, self.snapshot),
            ({OBSERVATION_KEY: {"observation_id": "frame-1"}}, self.observation, self.snapshot),
            ({OBSERVATION_KEY: other}, self.observation, self.snapshot),
            ({OBSERVATION_KEY: self.replacement}, self.observation, None),
            ({OBSERVATION_KEY: self.replacement}, self.observation, self.error),
            ({OBSERVATION_KEY: self.replacement}, None, self.snapshot),
        )
        for shared, observation, memory in cases:
            with self.subTest(shared=shared, observation=observation, memory=memory):
                self.assertIs(observation_after_memory(shared, observation, memory), observation)


if __name__ == "__main__":
    unittest.main()
