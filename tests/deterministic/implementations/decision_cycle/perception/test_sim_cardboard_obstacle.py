"""The cardboard plugin boxes visible blocks and leaves the beige floor alone."""

from __future__ import annotations

import unittest

import numpy as np

from autonomy.decision_cycle.perception.inputs import build_perception_request
from autonomy.decision_cycle.perception.runner import PerceptionRunner
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorFrame, SensorReading
from implementations.decision_cycle.catalog import step_plugins


_SKY = (215, 214, 211)
_FLOOR = (213, 197, 172)
_CARDBOARD = (158, 107, 56)
_DARK_FACE = (82, 51, 26)


def _scene(box: tuple[int, int, int, int] | None = None, color: tuple[int, int, int] = _CARDBOARD) -> np.ndarray:
    rgb = np.empty((120, 160, 3), dtype=np.uint8)
    rgb[:50] = _SKY
    rgb[50:] = _FLOOR
    if box is not None:
        x1, y1, x2, y2 = box
        rgb[y1:y2, x1:x2] = color
    return rgb


def _perceive(rgb: np.ndarray):
    reading = SensorReading(
        sensor_id=FRONT_CAMERA_SENSOR_ID,
        sensor_kind="camera",
        captured_at_ms=10,
        value=rgb,
        metadata={"color_space": "RGB"},
    )
    frame = SensorFrame(
        read_id="cardboard-test",
        readings={reading.sensor_id: reading},
        started_at_ms=10,
        completed_at_ms=10,
    )
    runner = PerceptionRunner.from_selection(
        plugins=["sim_cardboard_obstacle"],
        plugin_specs={
            "sim_cardboard_obstacle": step_plugins("perception")["sim_cardboard_obstacle"]["spec"]
        },
    )
    return runner.perceive(build_perception_request(frame))


def _contains(bbox: tuple[float, float, float, float], x: float, y: float) -> bool:
    x1, y1, x2, y2 = bbox
    return x1 <= x <= x2 and y1 <= y <= y2


class SimCardboardObstacleTests(unittest.TestCase):
    def test_each_separated_block_gets_a_box_including_the_smaller_distant_one(self) -> None:
        rgb = _scene()
        boxes = [(10, 20, 45, 75), (70, 40, 96, 65), (125, 60, 135, 66)]
        for x1, y1, x2, y2 in boxes:
            rgb[y1:y2, x1:x2] = _CARDBOARD
        result = _perceive(rgb)

        self.assertEqual(result.status, "ok")
        self.assertEqual(len(result.things), 3)
        self.assertEqual(len({thing.thing_id for thing in result.things}), 3)
        self.assertEqual(result.signals[0].properties["obstacle_count"], 3)
        for x1, y1, x2, y2 in boxes:
            center = ((x1 + x2) / (2 * 159), (y1 + y2) / (2 * 119))
            self.assertEqual(sum(_contains(thing.location.bbox_xyxy_norm, *center) for thing in result.things), 1)

    def test_touching_blocks_with_different_ground_edges_get_separate_boxes(self) -> None:
        rgb = _scene()
        boxes = [(20, 35, 50, 80), (45, 40, 80, 86), (75, 55, 115, 93)]
        for x1, y1, x2, y2 in boxes:
            rgb[y1:y2, x1:x2] = _CARDBOARD
        result = _perceive(rgb)

        self.assertEqual(result.status, "ok")
        self.assertEqual(len(result.things), 3)
        self.assertEqual(result.signals[0].properties["obstacle_count"], 3)

    def test_a_single_sloped_base_is_not_split_into_multiple_obstacles(self) -> None:
        rgb = _scene()
        for x in range(20, 130):
            rgb[25:65 + (x - 20) // 6, x] = _CARDBOARD
        result = _perceive(rgb)

        self.assertEqual(len(result.things), 1)
        self.assertEqual(result.signals[0].properties["obstacle_count"], 1)

    def test_brown_block_is_one_box_and_the_floor_is_not(self) -> None:
        found = _perceive(_scene((40, 30, 100, 70)))
        empty = _perceive(_scene())

        self.assertEqual(found.status, "ok")
        self.assertEqual(len(found.things), 1)
        thing = found.things[0]
        self.assertEqual(thing.kind, "obstruction_evidence")
        self.assertEqual(thing.thing_id, "cardboard_obstacle")
        bbox = thing.location.bbox_xyxy_norm
        self.assertIsNotNone(bbox)
        self.assertTrue(_contains(bbox, 70 / 159, 50 / 119))
        self.assertLess(bbox[3], 0.75)
        self.assertTrue(found.signals[0].value)
        self.assertEqual(found.signals[0].signal_id, "obstacle_in_sight")

        self.assertEqual(empty.status, "ok")
        self.assertEqual(empty.things, ())
        self.assertFalse(empty.signals[0].value)
        self.assertIsNone(empty.signals[0].properties["bbox_xyxy_norm"])

    def test_darker_cardboard_face_still_counts_and_beige_does_not(self) -> None:
        dark = _perceive(_scene((20, 40, 90, 90), _DARK_FACE))
        beige = _perceive(_scene((20, 40, 90, 90), _FLOOR))

        self.assertEqual(len(dark.things), 1)
        self.assertTrue(dark.signals[0].value)
        self.assertEqual(beige.things, ())
        self.assertFalse(beige.signals[0].value)
