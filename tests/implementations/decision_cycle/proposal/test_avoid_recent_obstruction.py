"""avoid_recent_obstruction reference plugin tests (M006-04)."""

from __future__ import annotations

import unittest

from autonomy.decision_cycle.proposal.inputs import build_decision_data_source
from autonomy.decision_cycle.memory.evidence import MemoryOrigin, RetainedEvidence
from autonomy.decision_cycle.perception.evidence.values import ViewLocation
from autonomy.decision_cycle.memory.publication import EVIDENCE_KEY
from implementations.decision_cycle.proposal.avoid_recent_obstruction.plugin import (
    AvoidRecentObstruction,
    propose as _propose,
)


def propose(inputs, **kwargs):
    """Call the plugin with a (source, shared_memory) pair, or a source and an empty map."""

    source, shared_memory = inputs if isinstance(inputs, tuple) else (inputs, {})
    return _propose(source, shared_memory, **kwargs)


def _record(
    *,
    kind: str = "floor_boundary",
    zone: str = "left",
    frame_id: str = "frame_001",
    updated_at_ms: int = 1000,
    confidence: float = 0.8,
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

    source = build_decision_data_source(frame_id=frame, frame_index=1, timestamp_ms=now)
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
        source = build_decision_data_source(frame_id="frame_001", frame_index=0, timestamp_ms=1)
        p = propose((source, {}))
        self.assertEqual(p.lifecycle, "missing_input")
        self.assertEqual(p.reason, "memory_unavailable")

    def test_malformed_evidence_value_is_error(self) -> None:
        source = build_decision_data_source(frame_id="frame_001", frame_index=0, timestamp_ms=1)
        p = propose((source, {EVIDENCE_KEY: "not evidence"}))
        self.assertEqual(p.lifecycle, "error")
        self.assertEqual(p.reason, "invalid_memory_value")

    def test_empty_memory_inactive(self) -> None:
        p = propose(_source(()))
        self.assertEqual(p.lifecycle, "inactive")

    def test_center_band_inactive(self) -> None:
        p = propose(
            _source(
                (
                    _record(
                        zone="center",
                        bbox=(0.45, 0.0, 0.55, 1.0),
                    ),
                )
            )
        )
        self.assertEqual(p.lifecycle, "inactive")

    def test_fresh_center_does_not_fall_back_to_retained_side(self) -> None:
        # Fresh pool exists (center-band cue only). Must not fall back to older retained left.
        fresh_center = _record(
            zone="center",
            bbox=(0.45, 0.0, 0.55, 1.0),
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
        self.assertEqual(p.lifecycle, "inactive")
        self.assertIsNone(p.command)

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


    def test_capabilities_unavailable_uses_configured_magnitude(self) -> None:
        from autonomy.decision_cycle.proposal.inputs import unavailable_envelope

        memory = {EVIDENCE_KEY: (_record(),)}
        source = build_decision_data_source(
            frame_id="frame_001",
            frame_index=0,
            timestamp_ms=1000,
            capabilities=unavailable_envelope(
                "stage_not_configured", updated_at_ms=1000
            ),
        )
        p = propose((source, memory), steer_magnitude=0.4)
        self.assertEqual(p.lifecycle, "fresh")
        assert p.command is not None
        self.assertAlmostEqual(p.command.steering, 0.4)
        self.assertIn("capabilities_not_ready", p.assumptions)

    def test_ready_capabilities_invalid_max_abs_steering(self) -> None:
        from autonomy.decision_cycle.proposal.inputs import (
            default_capabilities,
            ready_envelope,
        )

        memory = {EVIDENCE_KEY: (_record(),)}
        # Out-of-range / non-numeric fields rejected at source construction.
        for bad_max in (-0.1, 1.5, "high", None):
            with self.subTest(max_abs_steering=bad_max):
                caps = default_capabilities()
                caps["max_abs_steering"] = bad_max
                with self.assertRaises((TypeError, ValueError)):
                    build_decision_data_source(
                        frame_id="frame_001",
                        frame_index=0,
                        timestamp_ms=1000,
                                capabilities=ready_envelope(caps, updated_at_ms=1000),
                    )
        # Zero is legal at source ([0,1]) but not usable for active magnitude.
        caps = default_capabilities()
        caps["max_abs_steering"] = 0.0
        source = build_decision_data_source(
            frame_id="frame_001",
            frame_index=0,
            timestamp_ms=1000,
            capabilities=ready_envelope(caps, updated_at_ms=1000),
        )
        p = propose((source, memory))
        self.assertEqual(p.lifecycle, "error")
        self.assertEqual(p.reason, "invalid_capabilities")
        self.assertIsNone(p.command)

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
        # Round-trip through the source's evidence audit copy (detach) like production.
        source = build_decision_data_source(
            frame_id="frame_001",
            frame_index=0,
            timestamp_ms=1000,
            evidence=(record,),
        )
        # The detached copy must preserve the canonical unknown zone.
        detached_zone = source.evidence.value[0].location.zone
        self.assertEqual(detached_zone, "unknown")
        p = propose((source, {EVIDENCE_KEY: source.evidence.value}))
        self.assertEqual(p.lifecycle, "fresh")
        assert p.command is not None
        self.assertAlmostEqual(p.command.steering, 1.0)
        self.assertAlmostEqual(p.command.throttle, 0.60)
        self.assertEqual(p.command.gear, "forward")


    def test_loaded_plugin_matches_propose(self) -> None:
        plugin = AvoidRecentObstruction(steer_magnitude=0.5)
        self.assertEqual(plugin.plugin_id, "avoid_recent_obstruction")
        inputs = _source((_record(zone="left"),))
        self.assertEqual(
            plugin.propose(*inputs).to_dict(),
            propose(inputs, steer_magnitude=0.5).to_dict(),
        )


if __name__ == "__main__":
    unittest.main()
