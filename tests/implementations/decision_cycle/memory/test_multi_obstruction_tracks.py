from __future__ import annotations

import dataclasses
import unittest
from typing import Any

from autonomy.decision_cycle.memory.publication import EVIDENCE_KEY
from implementations.decision_cycle.memory.plugins.multi_obstruction_tracks.plugin import (
    LEDGER_KEY,
    MultiObstructionMemory,
)
from tests.implementations.decision_cycle.memory.replay_fixtures import (
    OBSTRUCTION_LEFT_EDGES,
    tracks_frame_inputs,
)


def _update(memory: MultiObstructionMemory, shared_memory: dict[str, Any], index: int) -> None:
    context, observation = tracks_frame_inputs(index, OBSTRUCTION_LEFT_EDGES[index], shared_memory)
    memory.update(context, observation)


class MultiObstructionMemoryTests(unittest.TestCase):
    def test_tracked_observation_replaces_the_perceived_one(self) -> None:
        memory, shared_memory = MultiObstructionMemory(), {}
        context, observation = tracks_frame_inputs(0, OBSTRUCTION_LEFT_EDGES[0], shared_memory)

        memory.update(context, observation)

        tracked = shared_memory["decision.observation"]
        self.assertEqual([thing["thing_id"] for thing in tracked.things], ["obstruction_track_000"])
        self.assertEqual(tracked.metadata["tracking"]["track_events"], {0: "new"})
        self.assertFalse(
            any(signal["signal_id"] == "multi_obstruction_candidates" for signal in tracked.signals)
        )

    def test_retained_evidence_is_published_beside_the_ledger(self) -> None:
        memory, shared_memory = MultiObstructionMemory(), {}

        _update(memory, shared_memory, 0)

        ledger = shared_memory[LEDGER_KEY]
        self.assertEqual(shared_memory[EVIDENCE_KEY], ledger.records)
        self.assertIn(
            "thing:0:21:obstruction_track_000",
            {record.record_id for record in ledger.records},
        )

    def test_lost_track_leaves_the_observation_but_keeps_its_evidence(self) -> None:
        memory, shared_memory = MultiObstructionMemory(), {}
        for index in range(7):
            _update(memory, shared_memory, index)

        self.assertEqual(
            shared_memory["decision.observation"].metadata["tracking"]["track_events"], {0: "lost"}
        )
        self.assertEqual(shared_memory["decision.observation"].things, ())
        self.assertIn(
            "thing:0:21:obstruction_track_000",
            {record.record_id for record in shared_memory[EVIDENCE_KEY]},
        )

    def test_frame_without_the_candidate_signal_drops_tracking_state(self) -> None:
        memory, shared_memory = MultiObstructionMemory(), {}
        _update(memory, shared_memory, 0)
        _update(memory, shared_memory, 1)
        self.assertTrue(all(key in shared_memory for key in memory.history_keys))
        context, observation = tracks_frame_inputs(2, OBSTRUCTION_LEFT_EDGES[2], shared_memory)

        memory.update(context, dataclasses.replace(observation, signals=()))

        self.assertFalse(any(key in shared_memory for key in memory.history_keys))
        self.assertNotIn("decision.observation", shared_memory)

    def test_reset_starts_a_new_empty_epoch_and_forgets_tracks(self) -> None:
        memory, shared_memory = MultiObstructionMemory(), {}
        _update(memory, shared_memory, 0)
        previous_epoch = shared_memory[LEDGER_KEY].epoch_id

        memory.reset(shared_memory)

        ledger = shared_memory[LEDGER_KEY]
        self.assertNotEqual(ledger.epoch_id, previous_epoch)
        self.assertEqual((ledger.health, ledger.record_count), ("empty", 0))
        self.assertEqual(shared_memory[EVIDENCE_KEY], ())
        self.assertFalse(any(key in shared_memory for key in memory.history_keys))
        self.assertNotIn("decision.observation", shared_memory)

    def test_requires_a_host_shared_memory_map(self) -> None:
        context, observation = tracks_frame_inputs(0, OBSTRUCTION_LEFT_EDGES[0], None)

        with self.assertRaisesRegex(ValueError, "requires a host shared-memory map"):
            MultiObstructionMemory().update(context, observation)


if __name__ == "__main__":
    unittest.main()
