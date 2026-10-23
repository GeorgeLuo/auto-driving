"""avoid_recent_obstruction reference plugin tests (M006-04)."""

from __future__ import annotations

import unittest

from autonomy.decision_cycle.context import DecisionFrameContext
from dataclasses import replace
from autonomy.decision_cycle.memory.evidence import MemoryOrigin, RetainedEvidence
from autonomy.decision_cycle.perception.evidence.values import ViewLocation
from autonomy.decision_cycle.memory.publication import EVIDENCE_KEY
from implementations.decision_cycle.proposal.plugins.avoid_recent_obstruction.plugin import (
    AvoidRecentObstruction,
    propose as _propose,
)


def propose(inputs, **kwargs):
    """Call the plugin with a (source, shared_memory) pair, or a source and an empty map."""

    source, shared_memory = inputs if isinstance(inputs, tuple) else (inputs, {})
    return _propose(replace(source, shared_memory=shared_memory), **kwargs)


def _record(
    *,
    kind: str = "floor_boundary",
    zone: str = "left",
    frame_id: str = "frame_001",
    updated_at_ms: int = 1000,
    confidence: float | None = 0.8,
    record_id: str = "thing:1:a",
    bbox: tuple[float, float, float, float] | None = (0.0, 0.0, 0.2, 0.5),
    location_frame: str = "image",
) -> RetainedEvidence:
    location = None
    if zone is not None or bbox is not None:
        location = ViewLocation(
            frame=location_frame,
            zone=zone,
            bbox_xyxy_norm=bbox,
        )
    return RetainedEvidence(
        record_id=record_id,
        kind=kind,
        label=kind,
        confidence=confidence,
        origin=MemoryOrigin(
            observation_id="obs",
            observed_id="ev",
            coordinate_frame=location_frame,
            observed_at_ms=updated_at_ms,
            updated_at_ms=updated_at_ms,
            source_plugin_id="src",
            frame_id=frame_id,
        ),
        location=location,
        properties={},
    )


def _source(records: tuple[RetainedEvidence, ...], *, now: int = 1000, frame: str = "frame_001"):
    """A source for the frame and a host map where memory published ``records``."""

    source = DecisionFrameContext(frame_id=frame, frame_index=1, timestamp_ms=now)
    return source, {EVIDENCE_KEY: tuple(records)}


class AvoidRecentObstructionTests(unittest.TestCase):
    def test_fresh_left_floor_boundary(self) -> None:
        p = propose(_source((_record(zone="left", frame_id="frame_001"),)))
        self.assertEqual(p.lifecycle, "fresh")
        self.assertIsNotNone(p.command)
        assert p.command is not None
        self.assertGreater(p.command.steering, 0)
        self.assertAlmostEqual(p.command.throttle, 0.60)
        self.assertEqual(p.command.gear, "forward")

    def test_right_obstacle_retained(self) -> None:
        p = propose(
            _source(
                (_record(kind="obstacle", zone="right", frame_id="old", updated_at_ms=500),),
                now=1000,
                frame="frame_002",
            )
        )
        self.assertEqual(p.lifecycle, "retained")
        assert p.command is not None
        self.assertLess(p.command.steering, 0)
        self.assertAlmostEqual(p.command.throttle, 0.60)
        self.assertEqual(p.command.gear, "forward")

    def test_obstruction_evidence_kind_accepted(self) -> None:
        p = propose(
            _source((_record(kind="obstruction_evidence", zone="left"),))
        )
        self.assertEqual(p.lifecycle, "fresh")

    def test_fresh_beats_stale_higher_confidence(self) -> None:
        stale = _record(
            zone="right",
            frame_id="old",
            updated_at_ms=0,
            confidence=0.99,
            record_id="thing:1:stale",
        )
        fresh = _record(
            zone="left",
            frame_id="frame_002",
            updated_at_ms=2000,
            confidence=0.5,
            record_id="thing:1:fresh",
        )
        p = propose(_source((stale, fresh), now=2000, frame="frame_002"))
        self.assertEqual(p.lifecycle, "fresh")
        assert p.command is not None
        self.assertGreater(p.command.steering, 0)

    def test_stale_only(self) -> None:
        p = propose(
            _source(
                (_record(frame_id="old", updated_at_ms=0),),
                now=5000,
                frame="frame_009",
            )
        )
        self.assertEqual(p.lifecycle, "stale")
        self.assertIsNone(p.command)

    def test_unscored_record_steers_without_a_score(self) -> None:
        p = propose(_source((_record(zone="left", confidence=None),)))
        self.assertEqual(p.lifecycle, "fresh")
        self.assertIsNone(p.confidence)
        assert p.command is not None
        self.assertGreater(p.command.steering, 0)

        stale = propose(
            _source((_record(confidence=None, updated_at_ms=0),), now=5000, frame="frame_009")
        )
        self.assertEqual(stale.lifecycle, "stale")
        self.assertIsNone(stale.confidence)

    def test_scored_record_outranks_unscored(self) -> None:
        unscored = _record(zone="right", confidence=None, record_id="thing:1:a")
        scored = _record(zone="left", confidence=0.1, record_id="thing:1:b")
        p = propose(_source((unscored, scored)))
        self.assertEqual(p.confidence, 0.1)
        assert p.command is not None
        self.assertGreater(p.command.steering, 0)

    def test_future_dated_provenance(self) -> None:
        p = propose(
            _source(
                (_record(updated_at_ms=5000, frame_id="frame_001"),),
                now=1000,
            )
        )
        self.assertEqual(p.lifecycle, "error")
        self.assertEqual(p.reason, "future_dated_provenance")

    def test_non_accepted_kind_inactive(self) -> None:
        p = propose(_source((_record(kind="surface", zone="left"),)))
        self.assertEqual(p.lifecycle, "inactive")

    def test_non_image_location_incompatible(self) -> None:
        p = propose(
            _source(
                (
                    _record(
                        kind="floor_boundary",
                        zone="left",
                        location_frame="map",
                    ),
                )
            )
        )
        self.assertEqual(p.lifecycle, "incompatible")

    def test_unpublished_evidence_is_missing_input(self) -> None:
        source = DecisionFrameContext(frame_id="frame_001", frame_index=0, timestamp_ms=1)
        p = propose((source, {}))
        self.assertEqual(p.lifecycle, "missing_input")
        self.assertEqual(p.reason, "memory_unavailable")

    def test_malformed_evidence_value_is_error(self) -> None:
        source = DecisionFrameContext(frame_id="frame_001", frame_index=0, timestamp_ms=1)
        p = propose((source, {EVIDENCE_KEY: "not evidence"}))
        self.assertEqual(p.lifecycle, "error")
        self.assertEqual(p.reason, "invalid_memory_value")

    def test_empty_memory_inactive(self) -> None:
        p = propose(_source(()))
        self.assertEqual(p.lifecycle, "inactive")

    def test_center_band_drives(self) -> None:
        cases = (
            ("center", (0.45, 0.0, 0.55, 1.0), "steer_away_right_obstruction"),
            ("mid_center", (0.29, 0.47, 0.65, 0.49), "steer_away_left_obstruction"),
        )
        for zone, bbox, reason in cases:
            with self.subTest(zone=zone):
                p = propose(_source((_record(zone=zone, bbox=bbox),)))
                self.assertEqual(p.lifecycle, "fresh")
                self.assertEqual(p.reason, reason)
                self.assertIsNotNone(p.command)
                assert p.command is not None
                self.assertGreater(p.command.throttle, 0.0)
                self.assertNotEqual(p.command.steering, 0.0)

    def test_fresh_center_does_not_fall_back_to_retained_side(self) -> None:
        # Fresh center leans right. Do not replace it with the older retained left.
        fresh_center = _record(
            zone="mid_center",
            bbox=(0.46, 0.0, 0.56, 1.0),
            frame_id="frame_002",
            updated_at_ms=2000,
            confidence=0.5,
            record_id="thing:1:center",
        )
        retained_left = _record(
            zone="left",
            frame_id="old",
            updated_at_ms=1000,
            confidence=0.99,
            record_id="thing:1:left",
        )
        p = propose(
            _source((fresh_center, retained_left), now=2000, frame="frame_002")
        )
        self.assertEqual(p.lifecycle, "fresh")
        self.assertEqual(p.reason, "steer_away_right_obstruction")
        self.assertIsNotNone(p.command)

    def test_chase_compound_zone_with_bbox_is_active(self) -> None:
        """Live floor_continuity zones are mid_left/near_right, not exact left/right."""

        p = propose(
            _source(
                (
                    _record(
                        kind="floor_boundary",
                        zone="mid_right",
                        bbox=(0.6432, 0.4739, 0.7027, 0.4823),
                        frame_id="frame_001",
                    ),
                )
            )
        )
        self.assertEqual(p.lifecycle, "fresh")
        self.assertTrue(p.available)
        self.assertIsNotNone(p.command)
        assert p.command is not None
        self.assertLess(p.command.steering, 0.0)
        self.assertEqual(p.reason, "steer_away_right_obstruction")

    def test_uppercase_zone_is_not_exact_lateral_cue(self) -> None:
        p = propose(
            _source(
                (
                    _record(
                        zone="LEFT",
                        bbox=None,
                        frame_id="frame_001",
                        updated_at_ms=1000,
                    ),
                ),
                now=1000,
                frame="frame_001",
            )
        )
        self.assertEqual(p.lifecycle, "inactive")
        self.assertIsNone(p.command)

    def test_center_without_bbox_never_enters_freshness(self) -> None:
        # No lateral cue → not an accepted candidate; do not emit stale/future paths.
        stale = propose(
            _source(
                (
                    _record(
                        zone="center",
                        bbox=None,
                        frame_id="old",
                        updated_at_ms=0,
                    ),
                ),
                now=5000,
                frame="current",
            )
        )
        self.assertEqual(stale.lifecycle, "inactive")
        self.assertIsNone(stale.command)

        future = propose(
            _source(
                (
                    _record(
                        zone="center",
                        bbox=None,
                        frame_id="old",
                        updated_at_ms=5000,
                    ),
                ),
                now=1000,
                frame="current",
            )
        )
        self.assertEqual(future.lifecycle, "inactive")
        self.assertNotEqual(future.reason, "future_dated_provenance")


    def test_steering_comes_from_plugin_config(self) -> None:
        source = DecisionFrameContext(frame_id="frame_001", frame_index=0, timestamp_ms=1000)
        for zone, expected in (("left", 0.25), ("right", -0.25)):
            with self.subTest(zone=zone):
                plugin = AvoidRecentObstruction(steer_magnitude=0.25)
                proposal = plugin.propose(replace(source, shared_memory={EVIDENCE_KEY: (_record(zone=zone),)}))
                self.assertAlmostEqual(proposal.command.steering, expected)
        self.assertNotIn("capabilities", source.to_dict())

    def test_invalid_steer_magnitude_is_rejected_in_config(self) -> None:
        for magnitude in (0, -0.1, 1.1, float("nan"), float("inf"), "high", None):
            with self.subTest(magnitude=magnitude), self.assertRaises(ValueError):
                AvoidRecentObstruction(steer_magnitude=magnitude)

    def test_missing_zone_bbox_only_is_active(self) -> None:
        """Omitted zone becomes ViewLocation 'unknown'; bbox mid_x still steers."""

        from autonomy.decision_cycle.perception.evidence.values import ViewLocation

        location = ViewLocation.from_dict(
            {
                "frame": "image",
                # zone omitted → canonical "unknown"
                "bbox_xyxy_norm": [0.0, 0.0, 0.2, 0.5],
            }
        )
        self.assertEqual(location.zone, "unknown")
        record = RetainedEvidence(
            record_id="thing:1:bbox",
            kind="obstacle",
            label="obstacle",
            confidence=0.8,
            origin=MemoryOrigin(
                observation_id="obs",
                observed_id="ev",
                coordinate_frame="image",
                observed_at_ms=1000,
                updated_at_ms=1000,
                source_plugin_id="src",
                frame_id="frame_001",
            ),
            location=location,
            properties={},
        )
        context = DecisionFrameContext("frame_001", 0, 1000, shared_memory={EVIDENCE_KEY: (record,)})
        self.assertEqual(record.location.zone, "unknown")
        p = _propose(context)
        self.assertEqual(p.lifecycle, "fresh")
        assert p.command is not None
        self.assertAlmostEqual(p.command.steering, 1.0)
        self.assertAlmostEqual(p.command.throttle, 0.60)
        self.assertEqual(p.command.gear, "forward")


    def test_loaded_plugin_matches_propose(self) -> None:
        plugin = AvoidRecentObstruction(steer_magnitude=0.5)
        self.assertEqual(plugin.plugin_id, "avoid_recent_obstruction")
        inputs = _source((_record(zone="left"),))
        source, shared_memory = inputs
        self.assertEqual(
            plugin.propose(replace(source, shared_memory=shared_memory)).to_dict(),
            propose(inputs, steer_magnitude=0.5).to_dict(),
        )


if __name__ == "__main__":
    unittest.main()
