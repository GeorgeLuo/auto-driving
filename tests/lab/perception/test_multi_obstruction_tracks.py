from __future__ import annotations

import unittest

from autonomy.perception import PerceivedThing, ViewLocation
from lab.plugins.perception.multi_obstruction_tracks.src.plugin import (
    MultiObstructionTracksPlugin,
)

from lab.plugins.memory.multi_obstruction_tracks.tracker import ObstructionTrackState, _Track


def _lookback_memory(tracks: list[dict]) -> dict:
    return {
        "records": [
            {
                "kind": "signal",
                "provenance": {"evidence_id": "multi_obstruction_track_lookback"},
                "properties": {"tracks": tracks},
            }
        ]
    }


def _region(
    thing_id: str,
    bbox: tuple[float, float, float, float],
    confidence: float = 0.8,
    *,
    touches_lower_image: bool = False,
) -> PerceivedThing:
    return PerceivedThing(
        thing_id=thing_id,
        kind="region_proposal",
        label="region",
        location=ViewLocation(
            frame="image",
            zone="mid_left",
            bbox_xyxy_norm=bbox,
        ),
        confidence=confidence,
        properties={
            "area_fraction": (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]),
            "color_coherence": 0.9,
            "solidity": 0.9,
            "touches_lower_image": touches_lower_image,
        },
    )


class MultiObstructionTracksTests(unittest.TestCase):
    def test_prior_memory_replaces_track_continuity(self) -> None:
        plugin = ObstructionTrackState()
        plugin._tracks[7] = _Track(
            track_id=7,
            bbox=(0.1, 0.1, 0.2, 0.2),
            confidence=0.4,
            shape_support=0.4,
            flow_support=0.0,
            age_frames=1,
        )
        self.assertEqual(plugin._restore_from_prior_memory(None), 0)
        self.assertIn(7, plugin._tracks)
        restored = plugin._restore_from_prior_memory(
            _lookback_memory(
                [
                    {
                        "slot": "active",
                        "track_id": 3,
                        "bbox": [0.2, 0.2, 0.4, 0.5],
                        "confidence": 0.8,
                        "shape_support": 0.6,
                        "flow_support": 0.1,
                        "age_frames": 4,
                        "missed_frames": 1,
                        "last_association_score": 0.2,
                        "lost_age": 0,
                    }
                ]
            )
        )
        self.assertEqual(restored, 1)
        self.assertEqual(list(plugin._tracks), [3])
        self.assertEqual(plugin._lookback_tracks()[0]["track_id"], 3)
        self.assertEqual(plugin._restore_from_prior_memory(_lookback_memory([])), 0)
        self.assertEqual(plugin._tracks, {})

    def test_floor_like_lower_region_is_suppressed(self) -> None:
        plugin = MultiObstructionTracksPlugin()
        kept = plugin._filter_candidates(
            (
                _region("box", (0.20, 0.18, 0.42, 0.62)),
                _region("carpet", (0.05, 0.70, 0.95, 0.98), touches_lower_image=True),
            )
        )
        self.assertEqual([thing.thing_id for thing in kept], ["box"])

    def test_two_tracks_are_preserved_and_associated(self) -> None:
        plugin = ObstructionTrackState(max_tracks=3)
        first, events, _ = plugin._associate(
            [
                _region("left", (0.18, 0.20, 0.38, 0.55)),
                _region("right", (0.66, 0.22, 0.84, 0.56)),
            ]
        )
        self.assertEqual(len(first), 2)
        self.assertEqual(set(events.values()), {"new"})

        second, events, _ = plugin._associate(
            [
                _region("left-next", (0.20, 0.22, 0.40, 0.57)),
                _region("right-next", (0.64, 0.23, 0.82, 0.58)),
            ]
        )
        self.assertEqual([track.track_id for track in second], [0, 1])
        self.assertEqual(set(events.values()), {"matched"})

    def test_lost_track_is_dropped_and_can_be_reacquired(self) -> None:
        plugin = ObstructionTrackState(max_missed_frames=0, reacquire_window_frames=2)
        first, _, _ = plugin._associate([_region("left", (0.18, 0.20, 0.38, 0.55))])
        self.assertEqual([track.track_id for track in first], [0])
        dropped, events, _ = plugin._associate([])
        self.assertEqual(dropped, [])
        self.assertEqual(events[0], "lost")
        reacquired, events, _ = plugin._associate([_region("left-return", (0.19, 0.21, 0.39, 0.56))])
        self.assertEqual([track.track_id for track in reacquired], [0])
        self.assertEqual(events[0], "reacquired")

    def test_lost_identity_expires_after_reacquire_window(self) -> None:
        plugin = ObstructionTrackState(max_missed_frames=0, reacquire_window_frames=1)
        plugin._associate([_region("first", (0.18, 0.20, 0.38, 0.55))])
        dropped, events, _ = plugin._associate([])
        self.assertEqual(dropped, [])
        self.assertEqual(events[0], "lost")
        plugin._associate([])
        replacement, events, _ = plugin._associate(
            [_region("replacement", (0.18, 0.20, 0.38, 0.55))]
        )
        self.assertEqual([track.track_id for track in replacement], [1])
        self.assertEqual(events[1], "new")

    def test_distant_same_area_region_does_not_inherit_identity(self) -> None:
        plugin = ObstructionTrackState(max_missed_frames=0, max_tracks=2)
        plugin._associate([_region("first", (0.10, 0.20, 0.30, 0.50))])
        active, events, _ = plugin._associate(
            [_region("far", (0.72, 0.20, 0.92, 0.50))]
        )
        self.assertEqual([track.track_id for track in active], [1])
        self.assertEqual(events[0], "lost")
        self.assertEqual(events[1], "new")

    def test_contract_declares_multi_track_obstacle_output(self) -> None:
        self.assertEqual(MultiObstructionTracksPlugin.contract.state_mode, "stateless")
        self.assertTrue(
            any(
                "region proposals" in emission
                for emission in MultiObstructionTracksPlugin.contract.emits
            )
        )

    def test_confidence_floor_only_suppresses_stale_events(self) -> None:
        plugin = ObstructionTrackState(minimum_output_confidence=0.43)
        observed = _Track(0, (0.1, 0.2, 0.3, 0.5), 0.20, 0.8, 0.0, 3)
        stale = _Track(1, (0.1, 0.2, 0.3, 0.5), 0.20, 0.8, 0.0, 3)
        self.assertTrue(plugin._should_emit_track(observed, "matched"))
        self.assertTrue(plugin._should_emit_track(observed, "reacquired"))
        self.assertFalse(plugin._should_emit_track(stale, "predicted"))
        self.assertFalse(plugin._should_emit_track(stale, "held"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
