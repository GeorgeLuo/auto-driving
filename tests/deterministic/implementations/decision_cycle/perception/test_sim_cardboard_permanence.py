"""Cardboard identities continue from host history across fresh instances."""

from __future__ import annotations

import unittest

import numpy as np

from autonomy.decision_cycle.perception.inputs import build_perception_request
from autonomy.decision_cycle.perception.runner import PerceptionRunner
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorFrame, SensorReading
from implementations.decision_cycle.catalog import step_plugins
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
    runner = PerceptionRunner.from_selection(
        plugins=["sim_cardboard_permanence"],
        plugin_specs={
            "sim_cardboard_permanence": step_plugins("perception")["sim_cardboard_permanence"]["spec"]
        },
    )
    # Every call uses a fresh runner: only the map can carry identity forward.
    return runner.perceive(build_perception_request(frame, shared_memory=memory))


class SimCardboardPermanenceTests(unittest.TestCase):
    def test_a_box_keeps_its_id_and_is_remembered_until_forgotten(self) -> None:
        memory = {}
        first = _perceive(memory, (40, 30, 100, 70))
        matched = _perceive(memory, (44, 30, 104, 70))
        self.assertEqual(first.status, "ok")
        self.assertEqual(first.things[0].properties["event"], "new")
        self.assertEqual(matched.things[0].thing_id, "cardboard_object_001")
        self.assertEqual(matched.things[0].properties["event"], "matched")
        self.assertEqual(matched.things[0].properties["seen_frames"], 2)
        self.assertIsNotNone(matched.things[0].confidence)
        for misses in range(1, 4):
            remembered = _perceive(memory, None)
            self.assertEqual(remembered.things[0].thing_id, "cardboard_object_001")
            self.assertEqual(remembered.things[0].properties["event"], "remembered")
            self.assertEqual(remembered.things[0].properties["missed_frames"], misses)
            self.assertIsNone(remembered.things[0].confidence)
            self.assertFalse(remembered.signals[0].value)
        forgotten = _perceive(memory, None)
        self.assertEqual(forgotten.things, ())
        self.assertEqual(forgotten.signals[-1].value, 0)
        self.assertEqual(forgotten.signals[-1].properties, {"objects_read": 1, "forgotten": [1]})
        self.assertEqual(_perceive(memory, (40, 30, 100, 70)).things[0].thing_id, "cardboard_object_002")

    def test_a_far_box_gets_a_new_id_while_the_old_one_is_remembered(self) -> None:
        memory = {}
        _perceive(memory, (10, 30, 40, 70))
        result = _perceive(memory, (120, 30, 150, 70))
        self.assertEqual(
            [(thing.thing_id, thing.properties["event"]) for thing in result.things],
            [("cardboard_object_001", "remembered"), ("cardboard_object_002", "new")],
        )

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
        self.assertEqual(len(matched.things), 1)
        self.assertEqual(matched.things[0].thing_id, "cardboard_object_007")
        self.assertEqual(matched.things[0].properties["event"], "matched")
        self.assertEqual(matched.things[0].properties["seen_frames"], 4)
        self.assertEqual(matched.signals[-1].properties["objects_read"], 1)
        far = _perceive(memory, (120, 30, 150, 70))
        self.assertEqual(far.things[-1].thing_id, "cardboard_object_008")

    def test_invalid_history_and_counter_fall_back_to_a_fresh_identity(self) -> None:
        for history in (None, [], {"objects": "bad", "next_object_id": "bad"}):
            with self.subTest(history=history):
                result = _perceive({KEY: history}, (40, 30, 100, 70))
                self.assertEqual(result.things[0].thing_id, "cardboard_object_001")

    def test_equal_distances_match_the_first_restored_object(self) -> None:
        objects = [
            {"object_id": object_id, "bbox_xyxy_norm": [0.25, 0.25, 0.62, 0.58],
             "seen_frames": 1, "missed_frames": 0}
            for object_id in (7, 2)
        ]
        result = _perceive({KEY: {"objects": objects}}, (40, 30, 100, 70))
        self.assertEqual([thing.properties["event"] for thing in result.things], ["matched", "remembered"])

    def test_reset_pops_its_history(self) -> None:
        plugin = SimCardboardPermanencePlugin()
        memory = {KEY: {"objects": []}, "other": 1}
        plugin.reset(memory)
        plugin.reset()
        self.assertEqual(memory, {"other": 1})
