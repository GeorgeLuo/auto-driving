"""The wall plugin reports one box per image strip whose lower edge follows the wall's base."""

import unittest

import numpy as np

from autonomy.decision_cycle.perception.inputs import build_perception_request
from autonomy.decision_cycle.perception.runner import PerceptionRunner
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorFrame, SensorReading
from implementations.decision_cycle.catalog import step_plugins
from implementations.decision_cycle.perception.plugins.sim_wall.plugin import SimWallPlugin

_WALL = (158, 150, 135)  # saturation 0.146
_LIT_WALL = (208, 195, 176)  # the same wall under brighter light: 0.154
_FLOOR = (133, 120, 103)  # 0.226


def _perceive(rgb, **config):
    reading = SensorReading(
        sensor_id=FRONT_CAMERA_SENSOR_ID, sensor_kind="camera", captured_at_ms=10,
        value=rgb, metadata={"color_space": "RGB"},
    )
    frame = SensorFrame(
        read_id="wall-test", readings={reading.sensor_id: reading},
        started_at_ms=10, completed_at_ms=10,
    )
    runner = PerceptionRunner.from_selection(
        plugins=["sim_wall"],
        plugin_specs={"sim_wall": step_plugins("perception")["sim_wall"]["spec"]},
        plugin_configs={"sim_wall": config} if config else None,
    )
    return runner.perceive(build_perception_request(frame))


def _sloped_wall(wall=_WALL):
    """A 160x120 view of a wall whose base drops from row 72 on the right to row 108 on the left."""

    rgb = np.full((120, 160, 3), _FLOOR, dtype=np.uint8)
    for x in range(160):
        rgb[:108 - (36 * x) // 160, x] = wall
    return rgb


class SimWallTests(unittest.TestCase):
    def test_each_strip_box_ends_at_the_wall_base(self):
        result = _perceive(_sloped_wall())
        boxes = [thing.location.bbox_xyxy_norm for thing in result.things]
        self.assertEqual(len(boxes), 16)
        self.assertEqual({thing.kind for thing in result.things}, {"obstruction_evidence"})
        self.assertEqual([box[0] for box in boxes], [index / 16 for index in range(16)])
        self.assertTrue(all(box[1] == 0.0 for box in boxes))
        bottoms = [box[3] for box in boxes]
        self.assertEqual(bottoms, sorted(bottoms, reverse=True))
        self.assertAlmostEqual(bottoms[0], 0.9, delta=0.01)
        self.assertAlmostEqual(bottoms[-1], 0.61, delta=0.01)
        signal = next(item for item in result.signals if item.signal_id == "wall_in_sight")
        self.assertIs(signal.value, True)
        self.assertEqual(signal.properties["segment_count"], 16)
        self.assertAlmostEqual(signal.properties["lowest_bottom_y"], bottoms[0])

    def test_brighter_light_on_the_same_wall_is_still_wall(self):
        bottoms = [thing.location.bbox_xyxy_norm[3] for thing in _perceive(_sloped_wall(_LIT_WALL)).things]
        self.assertEqual(len(bottoms), 16)
        self.assertAlmostEqual(bottoms[0], 0.9, delta=0.01)

    def test_the_longest_run_sets_the_box_and_thin_runs_are_ignored(self):
        rgb = np.full((120, 160, 3), _FLOOR, dtype=np.uint8)
        rgb[10:13] = _WALL  # a thin stray band
        rgb[30:80] = _WALL
        boxes = {thing.location.bbox_xyxy_norm[1:4:2] for thing in _perceive(rgb).things}
        self.assertEqual(len(boxes), 1)
        top, bottom = boxes.pop()
        self.assertAlmostEqual(top, 30 / 120, delta=0.01)
        self.assertAlmostEqual(bottom, 80 / 120, delta=0.01)
        rgb[30:80] = _FLOOR
        self.assertEqual(_perceive(rgb).things, ())
        self.assertEqual(len(_perceive(rgb, min_rows=2).things), 16)

    def test_floor_sky_and_cardboard_are_not_wall(self):
        rgb = np.full((120, 160, 3), (213, 197, 172), dtype=np.uint8)
        rgb[:45] = (215, 214, 211)
        rgb[40:90, 50:110] = (158, 107, 56)
        result = _perceive(rgb)
        self.assertEqual(result.things, ())
        signal = next(item for item in result.signals if item.signal_id == "wall_in_sight")
        self.assertIs(signal.value, False)
        self.assertIsNone(signal.properties["lowest_bottom_y"])

    def test_a_strip_needs_enough_wall_in_a_row(self):
        rgb = np.full((120, 160, 3), _FLOOR, dtype=np.uint8)
        rgb[:60, :4] = _WALL  # under half of the first 10-pixel strip
        self.assertEqual(_perceive(rgb).things, ())
        self.assertEqual(len(_perceive(rgb, row_fraction=0.3).things), 1)

    def test_invalid_configuration_is_rejected(self):
        for config in (
            {"min_saturation": -0.1}, {"max_saturation": 1.5}, {"min_saturation": True},
            {"min_saturation": 0.2, "max_saturation": 0.1}, {"strips": 0}, {"strips": 65},
            {"row_fraction": 0}, {"row_fraction": float("nan")}, {"min_rows": 0},
            {"working_width": 8},
        ):
            with self.subTest(config=config), self.assertRaises(ValueError):
                SimWallPlugin(**config)
