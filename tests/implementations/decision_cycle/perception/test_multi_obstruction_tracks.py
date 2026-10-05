from __future__ import annotations

import unittest
from typing import Any

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.memory.publication import EVIDENCE_KEY
from autonomy.decision_cycle.memory.runner import MemoryRunner
from autonomy.decision_cycle.observation.values import Observation
from implementations.decision_cycle.catalog import packaged_activation
from implementations.decision_cycle.perception.plugins.multi_obstruction_tracks.plugin import (
    MultiObstructionTracksPlugin,
)
from tests.implementations.decision_cycle.perception.tracks_replay import (
    OBSTRUCTION_LEFT_EDGES,
    load_baseline,
    perceive_frame,
    tracks_replay,
)

HISTORY_KEY = "perception.multi_obstruction_tracks.history"


def _frames(count: int) -> tuple[MultiObstructionTracksPlugin, dict[str, Any], list]:
    plugin, shared_memory = MultiObstructionTracksPlugin(), {}
    batches = [
        perceive_frame(plugin, index, OBSTRUCTION_LEFT_EDGES[index], shared_memory)
        for index in range(count)
    ]
    return plugin, shared_memory, batches


class MultiObstructionTracksTests(unittest.TestCase):
    def test_replay_matches_the_pinned_tracks(self) -> None:
        self.assertEqual(tracks_replay(), load_baseline("multi_obstruction_tracks"))

    def test_replay_repeats_exactly(self) -> None:
        self.assertEqual(tracks_replay(), tracks_replay())

    def test_first_frame_emits_a_new_track_and_its_signal(self) -> None:
        _plugin, _shared_memory, (batch,) = _frames(1)

        self.assertEqual([thing.thing_id for thing in batch.things], ["obstruction_track_000"])
        self.assertEqual(batch.measurements["track_events"], {0: "new"})
        (signal,) = batch.signals
        self.assertEqual((signal.signal_id, signal.value), ("multi_obstruction_tracks_available", True))
        self.assertNotIn("multi_obstruction_candidates", {s.signal_id for s in batch.signals})

    def test_history_lives_in_the_host_map(self) -> None:
        _plugin, shared_memory, _batches = _frames(2)

        history = shared_memory[HISTORY_KEY]
        self.assertEqual(set(history), {"tracks", "previous_gray", "next_track_id"})
        self.assertEqual(history["next_track_id"], 1)

    def test_a_replacement_plugin_continues_the_tracks_from_the_map(self) -> None:
        _plugin, shared_memory, _batches = _frames(3)

        batch = perceive_frame(MultiObstructionTracksPlugin(), 3, OBSTRUCTION_LEFT_EDGES[3], shared_memory)

        self.assertEqual([thing.thing_id for thing in batch.things], ["obstruction_track_000"])
        self.assertEqual(batch.measurements["track_events"], {0: "matched"})

    def test_lost_track_leaves_the_things_but_not_the_map(self) -> None:
        _plugin, shared_memory, batches = _frames(7)

        batch = batches[-1]
        self.assertEqual(batch.things, ())
        self.assertEqual(batch.measurements["track_events"], {0: "lost"})
        (signal,) = batch.signals
        self.assertFalse(signal.value)
        self.assertIn(HISTORY_KEY, shared_memory)

    def test_reset_drops_the_history(self) -> None:
        plugin, shared_memory, _batches = _frames(2)

        plugin.reset(shared_memory)

        self.assertNotIn(HISTORY_KEY, shared_memory)
        batch = perceive_frame(plugin, 2, OBSTRUCTION_LEFT_EDGES[2], shared_memory)
        self.assertEqual(batch.measurements["track_events"], {0: "new"})

    def test_requires_a_host_shared_memory_map(self) -> None:
        with self.assertRaisesRegex(ValueError, "shared"):
            perceive_frame(MultiObstructionTracksPlugin(), 0, OBSTRUCTION_LEFT_EDGES[0], None)


class TrackedEvidenceReachesMemoryTests(unittest.TestCase):
    """The proposal reads ``EVIDENCE_KEY``; memory only retains what perception tracked."""

    def _run(self, frame_count: int) -> dict[str, Any]:
        plugin, shared_memory = MultiObstructionTracksPlugin(), {}
        runner = MemoryRunner.from_activation(packaged_activation("memory"))
        for index in range(frame_count):
            batch = perceive_frame(plugin, index, OBSTRUCTION_LEFT_EDGES[index], shared_memory)
            observation = Observation(
                observation_id=f"frame_{index}",
                created_at_ms=100 + index * 100,
                sensor_frame={},
                things=tuple(thing.to_dict() for thing in batch.things),
                signals=tuple(signal.to_dict() for signal in batch.signals),
            )
            runner.update(
                DecisionFrameContext(
                    f"frame_{index}", index, 100 + index * 100, shared_memory=shared_memory
                ),
                observation,
            )
        return shared_memory

    def test_bounded_evidence_retains_the_tracked_thing(self) -> None:
        shared_memory = self._run(1)

        record_ids = {record.record_id for record in shared_memory[EVIDENCE_KEY]}
        self.assertTrue(any(record_id.endswith("obstruction_track_000") for record_id in record_ids))

    def test_retained_evidence_outlives_the_lost_track(self) -> None:
        shared_memory = self._run(7)

        record_ids = {record.record_id for record in shared_memory[EVIDENCE_KEY]}
        self.assertTrue(any(record_id.endswith("obstruction_track_000") for record_id in record_ids))


if __name__ == "__main__":
    unittest.main()
