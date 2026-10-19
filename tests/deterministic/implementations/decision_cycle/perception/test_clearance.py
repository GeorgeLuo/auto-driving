"""Clearance bands report only the detection plugins' boxes that are near enough."""

import unittest

from autonomy.decision_cycle.perception.evidence.values import PerceivedThing, ViewLocation
from autonomy.decision_cycle.perception.plugin import PerceptionPluginInputs
from implementations.decision_cycle.perception.plugins.clearance.plugin import ClearancePlugin


def _box(bbox, *, plugin_id="sim_cardboard_obstacle", kind="obstruction_evidence", index=0):
    return PerceivedThing(
        thing_id=f"box_{index}", kind=kind, label="cardboard",
        location=ViewLocation(frame="front_camera", zone="image", bbox_xyxy_norm=bbox),
        source_plugin_id=plugin_id,
    )


def _signals(*things, plugin=None):
    batch = (plugin or ClearancePlugin()).perceive(PerceptionPluginInputs(
        frame_id="frame_1", captured_at_ms=10, feeds={}, diagnostics=None, prior_things=things,
    ))
    return {signal.signal_id: signal for signal in batch.signals}


class ClearanceTests(unittest.TestCase):
    def test_no_boxes_is_clear_in_both_bands(self):
        signals = _signals()
        self.assertEqual(set(signals), {"clearance_ahead", "clearance_right"})
        for signal in signals.values():
            self.assertIs(signal.value, True)
            self.assertEqual(signal.properties["blocking_count"], 0)

    def test_a_near_box_blocks_each_band_it_overlaps(self):
        cases = {
            (0.35, 0.3, 0.6, 0.8): (False, True),   # centre only
            (0.5, 0.3, 0.9, 0.8): (False, False),   # centre and right
            (0.75, 0.3, 1.0, 0.8): (True, False),   # right only
            (0.0, 0.3, 0.25, 0.8): (True, True),    # left only
        }
        for bbox, (ahead, right) in cases.items():
            with self.subTest(bbox=bbox):
                signals = _signals(_box(bbox))
                self.assertIs(signals["clearance_ahead"].value, ahead)
                self.assertIs(signals["clearance_right"].value, right)

    def test_ahead_counts_farther_boxes_than_the_right(self):
        backed_away = _signals(_box((0.1, 0.3, 0.9, 0.58)))
        self.assertIs(backed_away["clearance_ahead"].value, False)
        self.assertIs(backed_away["clearance_right"].value, True)
        far = _signals(_box((0.1, 0.3, 0.9, 0.5)))
        self.assertIs(far["clearance_ahead"].value, True)
        self.assertIs(far["clearance_right"].value, True)

    def test_blocking_count_and_band_bounds_are_reported(self):
        signals = _signals(_box((0.4, 0.3, 0.5, 0.8), index=0), _box((0.6, 0.3, 0.8, 0.9), index=1))
        self.assertEqual(signals["clearance_ahead"].properties, {
            "min_x": 0.3, "max_x": 0.7, "bottom_y": 0.55, "blocking_count": 2,
        })
        self.assertEqual(signals["clearance_right"].properties, {
            "min_x": 0.65, "max_x": 1.0, "bottom_y": 0.6, "blocking_count": 1,
        })

    def test_other_plugins_kinds_and_unlocated_things_are_ignored(self):
        ignored = (
            _box((0.3, 0.3, 1.0, 0.9), plugin_id="other", index=0),
            _box((0.3, 0.3, 1.0, 0.9), kind="tracked_object", index=1),
            PerceivedThing(
                thing_id="box_2", kind="obstruction_evidence", label="cardboard",
                location=ViewLocation(frame="front_camera", zone="image"),
                source_plugin_id="sim_cardboard_obstacle",
            ),
        )
        for signal in _signals(*ignored).values():
            self.assertIs(signal.value, True)
        variant = ClearancePlugin(detection_plugin_ids=["other"])
        self.assertIs(_signals(*ignored, plugin=variant)["clearance_right"].value, False)

    def test_wall_segments_block_like_cardboard_boxes(self):
        signals = _signals(
            _box((0.0, 0.0, 0.5, 0.7), plugin_id="sim_wall", index=0),
            _box((0.75, 0.3, 0.9, 0.8), index=1),
        )
        self.assertEqual(signals["clearance_ahead"].properties["blocking_count"], 1)
        self.assertEqual(signals["clearance_right"].properties["blocking_count"], 1)

    def test_invalid_configuration_is_rejected(self):
        for config in (
            {"ahead_min_x": -0.1}, {"ahead_max_x": 1.5}, {"right_bottom_y": float("nan")},
            {"ahead_bottom_y": True}, {"right_min_x": "0.5"},
            {"ahead_min_x": 0.7, "ahead_max_x": 0.3}, {"right_min_x": 1.0},
            {"detection_plugin_ids": []}, {"detection_plugin_ids": "sim_wall"},
            {"detection_plugin_ids": ["sim_wall", ""]},
        ):
            with self.subTest(config=config), self.assertRaises(ValueError):
                ClearancePlugin(**config)
