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


class _VariantDetector(SimCardboardObstaclePlugin):
    plugin_id = "cardboard_variant"


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
    return _perceive_rgb(memory, _scene(box))


def _perceive_rgb(memory: dict, rgb: np.ndarray):
    reading = SensorReading(
        sensor_id=FRONT_CAMERA_SENSOR_ID,
        sensor_kind="camera",
        captured_at_ms=10,
        value=rgb,
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
    def assert_detection_pairs(self, result, count: int) -> None:
        self.assertEqual(result.status, "ok")
        detections = [thing for thing in result.things if thing.source_plugin_id == "sim_cardboard_obstacle"]
        identities = _identities(result)
        self.assertEqual(len(detections), count)
        self.assertEqual(len(identities), count)
        self.assertEqual(len({thing.thing_id for thing in identities}), count)
        self.assertEqual([thing.location for thing in identities], [thing.location for thing in detections])
        self.assertEqual([thing.confidence for thing in identities], [thing.confidence for thing in detections])

    def test_multiple_identities_survive_detection_order_changes(self) -> None:
        memory = {}
        first = _scene()
        first[30:60, 15:40] = (158, 107, 56)
        first[20:80, 100:145] = (158, 107, 56)
        second = _scene()
        second[20:85, 10:50] = (158, 107, 56)
        second[32:62, 110:140] = (158, 107, 56)
        initial = _perceive_rgb(memory, first)
        continued = _perceive_rgb(memory, second)

        self.assert_detection_pairs(initial, 2)
        self.assert_detection_pairs(continued, 2)
        self.assertEqual(
            [thing.properties["object_id"] for thing in _identities(initial)], [1, 2],
        )
        self.assertEqual(
            [thing.properties["object_id"] for thing in _identities(continued)], [2, 1],
        )
        self.assertTrue(all(thing.properties["event"] == "matched" for thing in _identities(continued)))
        self.assertTrue(all(thing.properties["seen_frames"] == 2 for thing in _identities(continued)))

    def test_one_seeded_identity_cannot_be_claimed_by_two_detections(self) -> None:
        memory = {KEY: {"objects": [{
            "object_id": 7, "bbox_xyxy_norm": [0.15, 0.25, 0.31, 0.58],
            "seen_frames": 3, "missed_frames": 0,
        }], "next_object_id": 8}}
        rgb = _scene()
        rgb[30:70, 24:49] = (158, 107, 56)
        rgb[30:70, 58:82] = (158, 107, 56)
        result = _perceive_rgb(memory, rgb)

        self.assert_detection_pairs(result, 2)
        self.assertEqual([thing.properties["object_id"] for thing in _identities(result)], [7, 8])
        self.assertEqual([thing.properties["event"] for thing in _identities(result)], ["matched", "new"])

    def test_three_seeded_objects_are_all_mapped_and_only_a_missing_object_ages(self) -> None:
        boxes = [(10, 30, 35, 70), (65, 40, 90, 75), (120, 50, 145, 80)]
        objects = [{
            "object_id": object_id,
            "bbox_xyxy_norm": [x1 / 159, y1 / 119, (x2 - 1) / 159, (y2 - 1) / 119],
            "seen_frames": 3, "missed_frames": 0,
        } for object_id, (x1, y1, x2, y2) in zip((7, 19, 23), boxes)]
        memory = {KEY: {"objects": list(reversed(objects)), "next_object_id": 24}}
        rgb = _scene()
        for x1, y1, x2, y2 in boxes:
            rgb[y1:y2, x1:x2] = (158, 107, 56)
        initial = _perceive_rgb(memory, rgb)
        self.assert_detection_pairs(initial, 3)
        self.assertEqual({thing.properties["object_id"] for thing in _identities(initial)}, {7, 19, 23})
        self.assertTrue(all(thing.properties["event"] == "matched" for thing in _identities(initial)))
        rgb[40:75, 65:90] = (213, 197, 172)
        missing = _perceive_rgb(memory, rgb)
        self.assert_detection_pairs(missing, 2)
        self.assertEqual({thing.properties["object_id"] for thing in _identities(missing)}, {7, 23})
        history = {item["object_id"]: item for item in memory[KEY]["objects"]}
        self.assertEqual(history[19]["missed_frames"], 1)
        self.assertEqual(history[7]["missed_frames"], 0)
        self.assertEqual(history[23]["missed_frames"], 0)

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
            ["cardboard_obstacle_000"],
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

    def test_permanence_reuses_the_earlier_detection_instead_of_running_it_again(self) -> None:
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

    def test_a_detector_variant_is_mapped_when_configured(self) -> None:
        specs = step_plugins("perception")
        frame = SensorFrame(
            read_id="renamed-detector",
            readings={FRONT_CAMERA_SENSOR_ID: SensorReading(
                sensor_id=FRONT_CAMERA_SENSOR_ID, sensor_kind="camera", captured_at_ms=10,
                value=_scene((40, 30, 100, 70)), metadata={"color_space": "RGB"},
            )},
            started_at_ms=10,
            completed_at_ms=10,
        )
        runner = PerceptionRunner.from_selection(
            plugins=["cardboard_variant", "sim_cardboard_permanence"],
            plugin_specs={
                "cardboard_variant": f"{__name__}:_VariantDetector",
                "sim_cardboard_permanence": specs["sim_cardboard_permanence"]["spec"],
            },
            plugin_configs={"sim_cardboard_permanence": {"detection_plugin_id": "cardboard_variant"}},
        )

        result = runner.perceive(build_perception_request(frame, shared_memory={}))

        self.assertEqual(result.status, "ok")
        self.assertEqual([thing.properties["object_id"] for thing in _identities(result)], [1])

    def test_reset_pops_its_history(self) -> None:
        plugin = SimCardboardPermanencePlugin()
        memory = {KEY: {"objects": []}, "other": 1}
        plugin.reset(memory)
        plugin.reset()
        self.assertEqual(memory, {"other": 1})
