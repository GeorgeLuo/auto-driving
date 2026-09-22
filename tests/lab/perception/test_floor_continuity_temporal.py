from __future__ import annotations

import unittest

from autonomy.perception import PerceivedThing, ViewLocation
from lab.plugins.perception.floor_continuity_temporal.src.plugin import (
    TemporalFloorContinuityPlugin,
)


def _boundary(
    thing_id: str,
    bbox: tuple[float, float, float, float],
    confidence: float,
) -> PerceivedThing:
    return PerceivedThing(
        thing_id=thing_id,
        kind="floor_boundary",
        label="boundary",
        location=ViewLocation(frame="image", zone="mid_left", bbox_xyxy_norm=bbox),
        confidence=confidence,
    )


class TemporalFloorContinuityTests(unittest.TestCase):
    def test_association_prefers_continuity_over_unrelated_confidence_spike(self) -> None:
        plugin = TemporalFloorContinuityPlugin(
            smoothing_alpha=0.5,
            association_distance=0.25,
        )

        first, _ = plugin._select(
            [_boundary("left-0", (0.18, 0.42, 0.36, 0.52), 0.70)]
        )
        self.assertIsNotNone(first)

        selected, info = plugin._select(
            [
                _boundary("left-1", (0.20, 0.43, 0.39, 0.53), 0.72),
                _boundary("right-1", (0.72, 0.44, 0.92, 0.56), 0.99),
            ]
        )
        self.assertIsNotNone(selected)
        self.assertEqual(info["reason"], "associated")
        self.assertTrue(selected.location.zone.endswith("left"))
        self.assertLess(selected.confidence, 0.99)

    def test_short_gap_holds_last_geometry_and_then_expires(self) -> None:
        plugin = TemporalFloorContinuityPlugin(max_hold_frames=1)
        first, _ = plugin._select(
            [_boundary("left-0", (0.18, 0.42, 0.36, 0.52), 0.80)]
        )
        self.assertIsNotNone(first)

        held, held_info = plugin._select([])
        self.assertIsNotNone(held)
        self.assertTrue(held_info["held"])
        self.assertEqual(held.properties["temporal_missed_frames"], 1)

        expired, expired_info = plugin._select([])
        self.assertIsNone(expired)
        self.assertEqual(expired_info["reason"], "no_candidate")

    def test_contract_declares_windowed_single_boundary_output(self) -> None:
        self.assertEqual(TemporalFloorContinuityPlugin.contract.state_mode, "windowed")
        self.assertIn("one image-space temporally smoothed floor_boundary record", TemporalFloorContinuityPlugin.contract.emits)


if __name__ == "__main__":
    unittest.main(verbosity=2)
