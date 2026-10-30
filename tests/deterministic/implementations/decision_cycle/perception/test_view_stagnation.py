"""Visual stagnation through the camera feed, catalog and shared-memory boundary."""

from __future__ import annotations

import json
import unittest

import numpy as np

from autonomy.decision_cycle.perception.inputs import build_perception_request
from autonomy.decision_cycle.perception.runner import PerceptionRunner
from autonomy.shared_memory import shared_memory_document, shared_memory_from_document
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorFrame, SensorReading
from implementations.decision_cycle.catalog import step_plugins
from implementations.decision_cycle.perception.plugins.view_stagnation.plugin import (
    HISTORY_KEY,
    RESULT_KEY,
    ViewStagnationPlugin,
)


def _scene(level=80):
    rgb = np.full((96, 128, 3), level, dtype=np.uint8)
    rgb[20:70, 30:95] = level + 60
    return rgb


def _perceive(memory, at_ms, rgb=None, *, frame_id=None, missing=False, config=None, completed_at_ms=None):
    reading = SensorReading(
        sensor_id=FRONT_CAMERA_SENSOR_ID, sensor_kind="camera", captured_at_ms=at_ms,
        value=_scene() if rgb is None else rgb, metadata={"color_space": "RGB"},
    )
    frame = SensorFrame(
        read_id=frame_id or f"frame_{at_ms}",
        readings={} if missing else {reading.sensor_id: reading},
        started_at_ms=at_ms,
        completed_at_ms=at_ms if completed_at_ms is None else completed_at_ms,
    )
    runner = PerceptionRunner.from_selection(
        plugins=["view_stagnation"],
        plugin_specs={"view_stagnation": step_plugins("perception")["view_stagnation"]["spec"]},
        plugin_configs={"view_stagnation": config or {}},
    )
    # A fresh plugin instance on every frame exercises host-owned history.
    return runner.perceive(build_perception_request(frame, shared_memory=memory))


def _signal(result):
    return next(signal for signal in result.signals if signal.signal_id == "view_static")


def _static_memory():
    memory = {}
    for at_ms in (100, 600, 1100):
        _perceive(memory, at_ms)
    return memory


class ViewStagnationTests(unittest.TestCase):
    def test_static_requires_duration_and_multiple_fresh_captures(self):
        memory = {}
        first = _signal(_perceive(memory, 100))
        second = _signal(_perceive(memory, 600))
        third = _signal(_perceive(memory, 1100))
        self.assertIsNone(first.value)
        self.assertIsNone(second.value)
        self.assertIs(third.value, True)
        self.assertEqual(third.source_plugin_id, "view_stagnation")
        self.assertEqual(memory[RESULT_KEY], {
            "frame_id": "frame_1100", "captured_at_ms": 1100,
            "status": "static", "reason": "low_visual_change", "view_static": True,
            "change_score": 0.0, "unchanged_for_ms": 1000, "sample_count": 3,
            "confidence": 1.0,
        })

    def test_two_samples_do_not_suffice_even_after_the_time_window(self):
        memory = {}
        _perceive(memory, 100)
        self.assertIsNone(_signal(_perceive(memory, 1600)).value)
        self.assertIs(_signal(_perceive(memory, 1700)).value, True)

    def test_visual_change_clears_static_immediately_then_rewarms(self):
        memory = _static_memory()
        moved = np.roll(_scene(), 35, axis=1)
        self.assertIs(_signal(_perceive(memory, 1200, moved)).value, False)
        self.assertEqual(memory[RESULT_KEY]["unchanged_for_ms"], 0)
        self.assertIsNone(_signal(_perceive(memory, 1700, moved)).value)
        self.assertIs(_signal(_perceive(memory, 2200, moved)).value, True)

    def test_anchor_catches_accumulated_slow_change(self):
        memory = {}
        values = [
            _signal(_perceive(memory, 100 + index * 500, _scene(80 + index * 2))).value
            for index in range(7)
        ]
        self.assertNotIn(True, values)
        self.assertIn(False, values)

    def test_small_sensor_noise_still_allows_static(self):
        memory = {}
        rng = np.random.default_rng(7)
        for at_ms in (100, 600, 1100):
            rgb = (_scene().astype(np.int16) + rng.integers(-2, 3, _scene().shape)).astype(np.uint8)
            result = _perceive(memory, at_ms, rgb)
        self.assertIs(_signal(result).value, True)
        self.assertLess(memory[RESULT_KEY]["change_score"], 0.01)

    def test_duplicate_or_nonincreasing_capture_clears_published_truth(self):
        for frame_id, at_ms in (("frame_1100", 1200), ("new_id", 1100), ("rewound", 50)):
            with self.subTest(frame_id=frame_id, at_ms=at_ms):
                memory = _static_memory()
                result = _perceive(memory, at_ms, frame_id=frame_id, completed_at_ms=9000)
                self.assertIsNone(_signal(result).value)
                self.assertEqual(memory[RESULT_KEY]["reason"], "non_fresh_frame")
                self.assertNotIn(HISTORY_KEY, memory)
                self.assertIsNone(_signal(_perceive(memory, at_ms + 100)).value)

    def test_capture_gap_starts_a_new_window(self):
        memory = _static_memory()
        result = _perceive(memory, 4000)
        self.assertIsNone(_signal(result).value)
        self.assertEqual(memory[RESULT_KEY]["reason"], "capture_gap")
        self.assertEqual(memory[RESULT_KEY]["unchanged_for_ms"], 0)

    def test_camera_shape_or_configuration_change_rewarms(self):
        for kwargs in ({"rgb": _scene()[:80]}, {"config": {"change_threshold": 0.02}}):
            with self.subTest(kwargs=tuple(kwargs)):
                memory = _static_memory()
                self.assertIsNone(_signal(_perceive(memory, 1200, **kwargs)).value)
                self.assertEqual(memory[RESULT_KEY]["sample_count"], 1)

    def test_missing_feed_clears_history_and_result_before_recovery(self):
        memory = _static_memory()
        result = _perceive(memory, 1200, missing=True)
        self.assertEqual(result.plugin_runs[0].status, "unavailable")
        self.assertNotIn(HISTORY_KEY, memory)
        self.assertIsNone(memory[RESULT_KEY]["view_static"])
        self.assertIsNone(_signal(_perceive(memory, 1300)).value)

    def test_explicit_reset_preserves_other_plugins(self):
        memory = _static_memory()
        memory["other.plugin"] = 42
        ViewStagnationPlugin().reset(memory)
        ViewStagnationPlugin().reset()
        self.assertNotIn(HISTORY_KEY, memory)
        self.assertIsNone(memory[RESULT_KEY]["view_static"])
        self.assertEqual(memory["other.plugin"], 42)

    def test_json_checkpoint_can_resume_in_a_new_instance(self):
        memory = {}
        _perceive(memory, 100)
        _perceive(memory, 600)
        restored = shared_memory_from_document(json.loads(json.dumps(shared_memory_document(memory))))
        original = _signal(_perceive(memory, 1100))
        resumed = _signal(_perceive(restored, 1100))
        self.assertEqual(original.to_dict(), resumed.to_dict())
        self.assertIs(resumed.value, True)
        self.assertEqual(shared_memory_document(memory), shared_memory_document(restored))

    def test_long_static_interval_keeps_one_small_anchor(self):
        memory = {}
        for index in range(150):
            _perceive(memory, 100 + index * 100)
        self.assertIs(memory[RESULT_KEY]["view_static"], True)
        self.assertLess(len(json.dumps(shared_memory_document(memory))), 6000)

    def test_invalid_configuration_is_rejected(self):
        for config in (
            {"change_threshold": 0}, {"change_threshold": float("nan")},
            {"change_threshold": float("inf")}, {"change_threshold": True},
            {"static_after_ms": 0}, {"min_samples": 1}, {"max_gap_ms": -1},
            {"thumbnail_width": 0}, {"thumbnail_height": 300}, {"min_samples": 3.5},
        ):
            with self.subTest(config=config), self.assertRaises(ValueError):
                ViewStagnationPlugin(**config)
