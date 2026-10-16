"""Cardboard identities continue from host history across fresh instances."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np

from autonomy.decision_cycle.perception.inputs import build_perception_request
from autonomy.decision_cycle.perception.runner import PerceptionRunner
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorFrame, SensorReading
from implementations.decision_cycle.catalog import step_plugins
from implementations.decision_cycle.perception.plugins.sim_cardboard_obstacle.plugin import (
    SimCardboardObstaclePlugin,
)
from implementations.decision_cycle.perception.plugins.sim_cardboard_permanence.plugin import (
    SimCardboardPermanencePlugin,
)

KEY = "perception.sim_cardboard_permanence.history"


def _scene(box: tuple[int, int, int, int] | None = None) -> np.ndarray:
    rgb = np.empty((120, 160, 3), dtype=np.uint8)
    rgb[:50] = (215, 214, 211)
    rgb[50:] = (213, 197, 172)
    if box is not None:
        x1, y1, x2, y2 = box
        rgb[y1:y2, x1:x2] = (158, 107, 56)
    return rgb


_PLUGINS = ("sim_cardboard_obstacle", "sim_cardboard_permanence")


def _perceive(memory: dict, box: tuple[int, int, int, int] | None):
    reading = SensorReading(
        sensor_id=FRONT_CAMERA_SENSOR_ID,
        sensor_kind="camera",
        captured_at_ms=10,
        value=_scene(box),
        metadata={"color_space": "RGB"},
    )
    frame = SensorFrame(
        read_id="permanence-test",
        readings={reading.sensor_id: reading},
        started_at_ms=10,
        completed_at_ms=10,
    )
    specs = step_plugins("perception")
    runner = PerceptionRunner.from_selection(
        plugins=list(_PLUGINS),
        plugin_specs={plugin_id: specs[plugin_id]["spec"] for plugin_id in _PLUGINS},
    )
    # Every call uses a fresh runner: only the map can carry identity forward.
    return runner.perceive(build_perception_request(frame, shared_memory=memory))


def _identities(result):
    return [
        thing for thing in result.things
        if thing.source_plugin_id == "sim_cardboard_permanence"
    ]


class SimCardboardPermanenceTests(unittest.TestCase):
    def assert_overlapping_boxes(self, result, object_id: int, event: str) -> None:
        self.assertEqual(result.status, "ok")
        self.assertEqual(len(result.things), 2)
        detection, identity = result.things
        self.assertEqual(detection.source_plugin_id, "sim_cardboard_obstacle")
        self.assertEqual(identity.source_plugin_id, "sim_cardboard_permanence")
        self.assertEqual(identity.location, detection.location)
        self.assertEqual(identity.confidence, detection.confidence)
        self.assertEqual(identity.properties["object_id"], object_id)
        self.assertEqual(identity.properties["event"], event)

    def test_a_box_keeps_its_id_and_is_remembered_until_forgotten(self) -> None:
        memory = {}
        first = _perceive(memory, (40, 30, 100, 70))
        matched = _perceive(memory, (44, 30, 104, 70))
        self.assertEqual(first.status, "ok")
        self.assertEqual(_identities(first)[0].properties["event"], "new")
        self.assertEqual(_identities(first)[0].label, "new cardboard")
        self.assertEqual(_identities(matched)[0].thing_id, "cardboard_object_001")
        self.assertEqual(_identities(matched)[0].label, "known cardboard 001")
        self.assertEqual(_identities(matched)[0].properties["event"], "matched")
        self.assertEqual(_identities(matched)[0].properties["seen_frames"], 2)
        self.assertIsNotNone(_identities(matched)[0].confidence)
        for misses in range(1, 4):
            held = _perceive(memory, None)
            self.assertEqual(held.things, ())
            self.assertEqual(memory[KEY]["objects"][0]["missed_frames"], misses)
            self.assertEqual(held.signals[-1].value, 1)
            self.assertFalse(held.signals[0].value)
        forgotten = _perceive(memory, None)
        self.assertEqual(forgotten.things, ())
        self.assertEqual(forgotten.signals[-1].value, 0)
        self.assertEqual(forgotten.signals[-1].properties, {"objects_read": 1, "forgotten": [1]})
        self.assertEqual(
            _identities(_perceive(memory, (40, 30, 100, 70)))[0].thing_id,
            "cardboard_object_002",
        )

    def test_a_jump_creates_a_new_identity_at_the_detection_without_drawing_the_old_box(self) -> None:
        memory = {}
        _perceive(memory, (10, 30, 40, 70))
        result = _perceive(memory, (120, 30, 150, 70))
        self.assertEqual(
            [thing.thing_id for thing in result.things if thing.source_plugin_id == "sim_cardboard_obstacle"],
            ["cardboard_obstacle"],
        )
        self.assert_overlapping_boxes(result, 2, "new")
        self.assertEqual(_identities(result)[0].label, "new cardboard")
        self.assertEqual([item["object_id"] for item in memory[KEY]["objects"]], [1, 2])
        self.assertEqual(memory[KEY]["objects"][0]["missed_frames"], 1)

    def test_detection_and_identity_boxes_overlap_after_motion_and_a_short_gap(self) -> None:
        memory = {}
        self.assert_overlapping_boxes(_perceive(memory, (40, 30, 100, 70)), 1, "new")
        self.assert_overlapping_boxes(_perceive(memory, (44, 30, 104, 70)), 1, "matched")
        held = _perceive(memory, None)
        self.assertEqual(held.things, ())
        self.assertEqual(memory[KEY]["objects"][0]["missed_frames"], 1)
        self.assert_overlapping_boxes(_perceive(memory, (48, 30, 108, 70)), 1, "matched")
        self.assertEqual(memory[KEY]["objects"][0]["seen_frames"], 3)

    def test_the_mapper_reuses_the_earlier_detection_instead_of_running_it_again(self) -> None:
        with patch.object(
            SimCardboardObstaclePlugin, "perceive", autospec=True,
            side_effect=SimCardboardObstaclePlugin.perceive,
        ) as detection:
            result = _perceive({}, (40, 30, 100, 70))
        self.assertEqual(detection.call_count, 1)
        self.assert_overlapping_boxes(result, 1, "new")

    def test_a_fresh_instance_continues_a_hand_written_history(self) -> None:
        valid = {"object_id": 7, "bbox_xyxy_norm": [0.25, 0.25, 0.62, 0.58],
                 "seen_frames": 3, "missed_frames": 0}
        memory = {KEY: {"objects": [
            "bad", {**valid, "object_id": True}, {**valid, "seen_frames": False},
            {**valid, "bbox_xyxy_norm": [0, 0, True, 1]},
            {**valid, "bbox_xyxy_norm": [0, 0, float("nan"), 1]},
            valid, {**valid, "seen_frames": 100},
        ], "next_object_id": 2}}
        matched = _perceive(memory, (40, 30, 100, 70))
        self.assertEqual(len(_identities(matched)), 1)
        self.assertEqual(_identities(matched)[0].thing_id, "cardboard_object_007")
        self.assertEqual(_identities(matched)[0].properties["event"], "matched")
        self.assertEqual(_identities(matched)[0].properties["seen_frames"], 4)
        self.assertEqual(matched.signals[-1].properties["objects_read"], 1)
        far = _perceive(memory, (120, 30, 150, 70))
        self.assert_overlapping_boxes(far, 8, "new")
        self.assertEqual(_identities(far)[0].label, "new cardboard")

    def test_an_expired_seed_is_forgotten_instead_of_matching(self) -> None:
        memory = {KEY: {"objects": [{
            "object_id": 7, "bbox_xyxy_norm": [0.25, 0.25, 0.62, 0.58],
            "seen_frames": 9, "missed_frames": 4,
        }], "next_object_id": 8}}
        result = _perceive(memory, (40, 30, 100, 70))
        self.assert_overlapping_boxes(result, 8, "new")
        self.assertEqual(result.signals[-1].properties["forgotten"], [7])

    def test_invalid_history_and_counter_fall_back_to_a_fresh_identity(self) -> None:
        for history in (None, [], {"objects": "bad", "next_object_id": "bad"}):
            with self.subTest(history=history):
                result = _perceive({KEY: history}, (40, 30, 100, 70))
                self.assertEqual(_identities(result)[0].thing_id, "cardboard_object_001")

    def test_a_far_detection_does_not_take_a_second_held_identity(self) -> None:
        objects = [
            {"object_id": 7, "bbox_xyxy_norm": [0.05, 0.2, 0.2, 0.5],
             "seen_frames": 2, "missed_frames": 0},
            {"object_id": 2, "bbox_xyxy_norm": [0.1, 0.2, 0.25, 0.5],
             "seen_frames": 2, "missed_frames": 0},
        ]
        result = _perceive({KEY: {"objects": objects, "next_object_id": 8}}, (120, 30, 150, 70))
        self.assertEqual(
            [(thing.thing_id, thing.properties["event"], thing.label) for thing in _identities(result)],
            [("cardboard_object_008", "new", "new cardboard")],
        )

    def test_equal_distances_match_the_first_restored_object(self) -> None:
        objects = [
            {"object_id": object_id, "bbox_xyxy_norm": [0.25, 0.25, 0.62, 0.58],
             "seen_frames": 1, "missed_frames": 0}
            for object_id in (7, 2)
        ]
        memory = {KEY: {"objects": objects, "next_object_id": 8}}
        result = _perceive(memory, (40, 30, 100, 70))
        self.assertEqual(
            [(thing.thing_id, thing.properties["event"]) for thing in _identities(result)],
            [("cardboard_object_007", "matched")],
        )
        self.assertEqual(sorted(item["object_id"] for item in memory[KEY]["objects"]), [2, 7])

    def test_reset_pops_its_history(self) -> None:
        plugin = SimCardboardPermanencePlugin()
        memory = {KEY: {"objects": []}, "other": 1}
        plugin.reset(memory)
        plugin.reset()
        self.assertEqual(memory, {"other": 1})
