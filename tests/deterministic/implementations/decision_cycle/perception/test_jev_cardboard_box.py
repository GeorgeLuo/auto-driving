"""Jev detections must distinguish absence from provider failure and forced boxes."""

from __future__ import annotations

import io
import json
import unittest
from unittest import mock

import numpy as np
from PIL import Image
import requests

from autonomy.decision_cycle.perception.inputs import build_perception_request
from autonomy.decision_cycle.perception.runner import PerceptionRunner
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorFrame, SensorReading
from implementations.decision_cycle.catalog import selection_activation


def _response(presence=0.97, confidence=0.91):
    return {
        "model": "jev-1.14.0",
        "answers": {
            "has_box": {"type": "noul", "noul": presence},
            "box": {"type": "box", "confidence": confidence,
                    "box": {"left": 0.2, "top": 0.3, "right": 0.6, "bottom": 0.7}},
        },
        "usage": {"input_tokens": 1171, "output_tokens": 37},
    }


def _runner():
    return PerceptionRunner.from_activation(selection_activation("perception", plugins=["jev_cardboard_box"]))


def _request():
    reading = SensorReading(
        sensor_id=FRONT_CAMERA_SENSOR_ID, sensor_kind="camera", captured_at_ms=10,
        value=np.zeros((120, 160, 3), dtype=np.uint8), metadata={"color_space": "RGB"},
    )
    return build_perception_request(SensorFrame(
        read_id="jev-test", readings={reading.sensor_id: reading}, started_at_ms=10, completed_at_ms=10,
    ))


class JevCardboardBoxTests(unittest.TestCase):
    def _perceive(self, payload=None, status=200):
        response = mock.Mock(status_code=status)
        response.json.return_value = _response() if payload is None else payload
        with (
            mock.patch.dict("os.environ", {"JEV_API_KEY": "test-only-secret"}),
            mock.patch("requests.Session.post", return_value=response) as post,
        ):
            result = _runner().perceive(_request())
        return result, post

    def test_visible_box_emits_normalized_obstruction_and_presence_in_one_request(self):
        result, post = self._perceive()
        self.assertEqual(result.status, "ok")
        self.assertEqual(len(result.things), 1)
        self.assertEqual(result.things[0].kind, "obstruction_evidence")
        self.assertEqual(result.things[0].location.bbox_xyxy_norm, (0.2, 0.3, 0.6, 0.7))
        self.assertTrue(next(s for s in result.signals if s.signal_id == "has_box").value)
        post.assert_called_once()
        args = post.call_args.kwargs
        request = json.loads(args["files"][0][1][1])
        self.assertEqual(request["model"], "jev-1.14.0")
        self.assertEqual(set(request["questions"]), {"has_box", "box"})
        self.assertEqual(args["headers"]["TypeSafe-Danger-Zone-Images"], "true")
        self.assertFalse(args["allow_redirects"])
        image = Image.open(io.BytesIO(args["files"][1][1][1]))
        self.assertGreaterEqual(min(image.size), 280)

    def test_absent_target_suppresses_the_forced_high_confidence_box(self):
        result, _ = self._perceive(_response(presence=0.01, confidence=0.99))
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.things, ())
        self.assertTrue(all(s.value is False for s in result.signals))

    def test_present_target_with_uncertain_localization_does_not_emit_coordinates(self):
        result, _ = self._perceive(_response(confidence=0.2))
        self.assertEqual(result.things, ())
        signals = {s.signal_id: s.value for s in result.signals}
        self.assertTrue(signals["has_box"])
        self.assertFalse(signals["obstacle_in_sight"])

    def test_invalid_coordinates_are_errors_not_clear_path_signals(self):
        for coordinates in (
            {"left": 0.8, "right": 0.2}, {"top": float("nan")}, {"bottom": 1.2}, {"left": True},
        ):
            with self.subTest(coordinates=coordinates):
                payload = _response()
                payload["answers"]["box"]["box"].update(coordinates)
                result, _ = self._perceive(payload)
                self.assertNotEqual(result.status, "ok")
                self.assertEqual(result.things, ())
                self.assertEqual(result.signals, ())

    def test_rate_limit_is_error_without_false_absence_or_retry(self):
        result, post = self._perceive(status=429)
        self.assertNotEqual(result.status, "ok")
        self.assertEqual(result.signals, ())
        post.assert_called_once()

    def test_transport_failure_does_not_publish_credentials_from_exception(self):
        with (
            mock.patch.dict("os.environ", {"JEV_API_KEY": "test-only-secret"}),
            mock.patch("requests.Session.post", side_effect=requests.Timeout("test-only-secret")),
        ):
            result = _runner().perceive(_request())
        self.assertNotEqual(result.status, "ok")
        self.assertNotIn("test-only-secret", str(result))
        self.assertEqual(result.signals, ())

    def test_missing_credentials_fail_before_network_access(self):
        with (
            mock.patch.dict("os.environ", {"JEV_API_KEY": "", "TYPESAFE_API_KEY": ""}),
            mock.patch("requests.Session.post") as post,
        ):
            result = _runner().perceive(_request())
        post.assert_not_called()
        self.assertNotEqual(result.status, "ok")
        self.assertEqual(result.signals, ())
