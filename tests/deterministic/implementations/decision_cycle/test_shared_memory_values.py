"""Stateful plugins keep shared-memory values a document can round-trip."""

from __future__ import annotations

import json
import unittest
from typing import Any

import numpy as np

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.perception.diagnostics.sink import PerceptionDiagnosticSink
from autonomy.decision_cycle.perception.evidence.values import PerceptionEvidenceBatch
from autonomy.decision_cycle.perception.plugin import (
    PerceptionPluginInputs,
    PerceptionPluginWarmingUp,
)
from autonomy.shared_memory import shared_memory_document, shared_memory_from_document
from implementations.decision_cycle.memory.plugins.bounded_evidence.plugin import (
    LEDGER_KEY,
    BoundedEvidenceLedger,
)
from implementations.decision_cycle.perception.feeds.camera import CameraFrame
from implementations.decision_cycle.perception.plugins.floor_continuity_temporal.plugin import (
    TemporalFloorContinuityPlugin,
)
from implementations.decision_cycle.perception.plugins.motion_tracks.plugin import (
    MotionTracksPlugin,
)
from implementations.decision_cycle.perception.plugins.multi_obstruction_tracks.plugin import (
    MultiObstructionTracksPlugin,
)
from implementations.decision_cycle.perception.shared.image.stored import StoredImage
from tests.deterministic.implementations.decision_cycle.perception.tracks_replay import (
    OBSTRUCTION_LEFT_EDGES,
    perceive_frame,
)


class _QuietDiagnostics(PerceptionDiagnosticSink):
    def __init__(self) -> None:
        super().__init__(output_dir=None, plugin_id="replay", allowed_artifacts=())


def _round_trip(memory: dict[str, Any]) -> dict[str, Any]:
    return shared_memory_from_document(json.loads(json.dumps(shared_memory_document(memory))))


def _emitted(batch: PerceptionEvidenceBatch) -> dict[str, Any]:
    return {
        "things": [thing.to_dict() for thing in batch.things],
        "signals": [signal.to_dict() for signal in batch.signals],
    }


def _inputs(index: int, rgb: np.ndarray, memory: dict[str, Any]) -> PerceptionPluginInputs:
    timestamp_ms = 100 + index * 100
    frame = CameraFrame(
        "front_camera",
        timestamp_ms,
        np.ascontiguousarray(rgb),
        None,
        {"source_id": f"frame_{index}"},
    )
    return PerceptionPluginInputs(
        f"frame_{index}",
        timestamp_ms,
        {"frame": frame},
        _QuietDiagnostics(),
        {"sequence_index": index},
        shared_memory=memory,
    )


def _observation(observation_id: str, created_at_ms: int, thing_id: str) -> Observation:
    return Observation(
        observation_id=observation_id,
        created_at_ms=created_at_ms,
        sensor_frame={},
        perception_plugin_id="lightweight_observer",
        summary=("test",),
        things=(
            {
                "thing_id": thing_id,
                "kind": "floor_boundary",
                "label": thing_id,
                "confidence": 0.9,
                "location": {
                    "frame": "image",
                    "zone": "center",
                    "bbox_xyxy_norm": [0.4, 0.5, 0.6, 0.9],
                },
                "properties": {},
                "source_plugin_id": "floor_plane",
            },
        ),
        signals=(),
    )


class SharedMemoryValueTests(unittest.TestCase):
    def test_stored_image_decodes_to_a_writable_copy(self) -> None:
        image = StoredImage(np.arange(6, dtype=np.uint8).reshape(2, 3))

        restored = StoredImage.from_dict(image.to_dict())
        restored.pixels[0, 0] = 9

        self.assertTrue(restored.pixels.flags.writeable)
        self.assertEqual(int(image.pixels[0, 0]), 0)

    def test_multi_obstruction_tracks_match_after_a_round_trip(self) -> None:
        plugin = MultiObstructionTracksPlugin()
        memory: dict[str, Any] = {}
        for index, edge in enumerate(OBSTRUCTION_LEFT_EDGES[:4]):
            perceive_frame(plugin, index, edge, memory)
        restored = _round_trip(memory)

        original = perceive_frame(plugin, 4, OBSTRUCTION_LEFT_EDGES[4], memory)
        replayed = perceive_frame(plugin, 4, OBSTRUCTION_LEFT_EDGES[4], restored)

        self.assertEqual(_emitted(original), _emitted(replayed))

    def test_multi_obstruction_next_id_stays_above_the_restored_tracks(self) -> None:
        """Regression: a recorded next id below a restored track, or not a number, was kept."""

        plugin = MultiObstructionTracksPlugin()
        track = {"slot": "lost", "track_id": 4, "bbox": [0.1, 0.1, 0.3, 0.3], "lost_age": 1}
        for recorded in (2, "nope"):
            with self.subTest(next_track_id=recorded):
                memory: dict[str, Any] = {
                    plugin._memory_key: {"tracks": [track], "previous_gray": None, "next_track_id": recorded}
                }

                perceive_frame(plugin, 0, OBSTRUCTION_LEFT_EDGES[0], memory)

                history = memory[plugin._memory_key]
                self.assertGreater(history["next_track_id"], 4)
                ids = [item["track_id"] for item in history["tracks"]]
                self.assertEqual(len(ids), len(set(ids)))

    def test_motion_tracks_match_after_a_round_trip(self) -> None:
        rgb = np.random.default_rng(7).integers(0, 256, (72, 96, 3), dtype=np.uint8)
        plugin = MotionTracksPlugin(max_features=50, search_radius=8, min_group_size=4)
        memory: dict[str, Any] = {}
        with self.assertRaises(PerceptionPluginWarmingUp):
            plugin.perceive(_inputs(0, rgb, memory))
        plugin.perceive(_inputs(1, np.roll(rgb, 2, axis=1), memory))
        restored = _round_trip(memory)
        frame = np.roll(rgb, 4, axis=1)

        original = plugin.perceive(_inputs(2, frame, memory))
        replayed = plugin.perceive(_inputs(2, frame, restored))

        self.assertEqual(_emitted(original), _emitted(replayed))

    def test_motion_history_drops_invalid_tracks_and_keeps_the_next_id_above_them(self) -> None:
        plugin = MotionTracksPlugin()

        plugin._restore_history(
            {
                "previous_rgb": None,
                "tracks": [
                    "nope",
                    {
                        "track_id": 4,
                        "bbox": [0.1, 0.1, 0.2, 0.2],
                        "age_frames": 1,
                        "support_frames": 1,
                        "missed_frames": 0,
                        "confidence": 0.5,
                        "kind_hint": "motion",
                        "properties": {},
                    },
                ],
                "next_track_id": 2,
            }
        )

        self.assertEqual(set(plugin._tracks), {4})
        self.assertEqual(plugin._next_track_id, 5)

    def test_floor_continuity_matches_after_a_round_trip(self) -> None:
        scene = np.zeros((72, 96, 3), dtype=np.uint8)
        scene[:40] = (180, 190, 200)
        scene[40:] = (140, 120, 90)
        scene[30:55, 40:70] = (40, 40, 40)
        plugin = TemporalFloorContinuityPlugin()
        memory: dict[str, Any] = {}
        plugin.perceive(_inputs(0, scene, memory))
        plugin.perceive(_inputs(1, np.roll(scene, 3, axis=1), memory))
        restored = _round_trip(memory)
        frame = np.roll(scene, 6, axis=1)

        original = plugin.perceive(_inputs(2, frame, memory))
        replayed = plugin.perceive(_inputs(2, frame, restored))

        self.assertEqual(_emitted(original), _emitted(replayed))

    def test_floor_history_restores_only_its_named_fields(self) -> None:
        plugin = TemporalFloorContinuityPlugin()
        alpha = plugin.smoothing_alpha
        hold = plugin.max_hold_frames

        plugin._restore_history(
            {
                "last_bbox": [0.1, 0.2, 0.3, 0.4],
                "last_confidence": 0.5,
                "misses": 1,
                "age": 3,
                "smoothing_alpha": 0.99,
                "max_hold_frames": 0,
            }
        )

        self.assertEqual(plugin._last_bbox, (0.1, 0.2, 0.3, 0.4))
        self.assertEqual((plugin._last_confidence, plugin._misses, plugin._age), (0.5, 1, 3))
        self.assertEqual(plugin.smoothing_alpha, alpha)
        self.assertEqual(plugin.max_hold_frames, hold)

    def test_bounded_evidence_matches_after_a_round_trip(self) -> None:
        plugin = BoundedEvidenceLedger(max_records=8)
        memory: dict[str, Any] = {}
        plugin.update(
            DecisionFrameContext("f1", 1, 100, shared_memory=memory),
            _observation("o1", 90, "a"),
        )
        plugin.update(
            DecisionFrameContext("f2", 2, 200, shared_memory=memory),
            _observation("o2", 190, "b"),
        )
        restored = _round_trip(memory)
        continued = BoundedEvidenceLedger(max_records=8)

        plugin.update(
            DecisionFrameContext("f3", 3, 300, shared_memory=memory),
            _observation("o3", 290, "c"),
        )
        continued.update(
            DecisionFrameContext("f3", 3, 300, shared_memory=restored),
            _observation("o3", 290, "c"),
        )

        self.assertEqual(memory[LEDGER_KEY].to_dict(), restored[LEDGER_KEY].to_dict())
