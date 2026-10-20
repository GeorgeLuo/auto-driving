"""Count-gated Jev obstacle boxes must preserve the actual returned geometry."""

import json
import unittest
from unittest import mock

import numpy as np

from autonomy.decision_cycle.perception.diagnostics.sink import PerceptionDiagnosticSink
from autonomy.decision_cycle.perception.plugin import PerceptionPluginInputs
from implementations.decision_cycle.perception.feeds.camera import CameraFrame
from implementations.decision_cycle.perception.plugins.jev_discrete_obstacles.plugin import (
    JevDiscreteObstaclesPlugin,
)


def _payload(count):
    answers = {"obstacle_count": {
        "type": "choice", "choice": str(count), "confidence": 0.8,
        "probabilities": {str(i): float(i == count) for i in range(7)},
    }}
    for i in range(1, 7):
        answers[f"obstacle_{i}"] = {
            "type": "box", "confidence": 0.2,
            "box": {"left": 0.1, "top": 0.2, "right": 0.5, "bottom": 0.7},
        }
    return {"model": "jev-1.14.0", "answers": answers, "usage": {"input_tokens": 2000}}


def _inputs():
    return PerceptionPluginInputs(
        frame_id="test", captured_at_ms=0,
        feeds={"frame": CameraFrame("camera", 0, np.zeros((480, 640, 3), dtype=np.uint8))},
        diagnostics=PerceptionDiagnosticSink(
            output_dir=None, plugin_id="jev_discrete_obstacles", allowed_artifacts=(),
        ),
    )


class JevDiscreteObstaclesTests(unittest.TestCase):
    def _run(self, payload):
        response = mock.Mock(status_code=200)
        response.json.return_value = payload
        with (
            mock.patch.dict("os.environ", {"JEV_API_KEY": "test-only-key"}),
            mock.patch("requests.Session.post", return_value=response) as post,
        ):
            result = JevDiscreteObstaclesPlugin().perceive(_inputs())
        return result, post

    def test_count_selects_two_boxes_preserving_overlap_and_low_confidence(self):
        result, post = self._run(_payload(2))
        self.assertEqual(len(result.things), 2)
        self.assertEqual(result.things[0].location.bbox_xyxy_norm, (0.1, 0.2, 0.5, 0.7))
        self.assertEqual(result.things[1].location.bbox_xyxy_norm, (0.1, 0.2, 0.5, 0.7))
        self.assertEqual(result.things[0].confidence, 0.2)
        self.assertTrue(result.signals[0].value)
        post.assert_called_once()
        body = json.loads(post.call_args.kwargs["files"][0][1][1])
        self.assertIn("eye-level", body["state"]["camera_context"])
        self.assertIn("impede", body["state"]["task"])
        self.assertNotIn("cardboard", json.dumps(body))
        self.assertEqual(len(body["questions"]), 7)

    def test_zero_count_suppresses_all_always_returned_box_answers(self):
        result, _ = self._run(_payload(0))
        self.assertEqual(result.things, ())
        self.assertFalse(result.signals[0].value)

    def test_invalid_selected_geometry_is_an_error(self):
        payload = _payload(2)
        payload["answers"]["obstacle_2"]["box"]["left"] = float("nan")
        with self.assertRaisesRegex(RuntimeError, "invalid obstacle response"):
            self._run(payload)

    def test_unselected_geometry_is_not_interpreted_as_a_detection(self):
        payload = _payload(1)
        payload["answers"]["obstacle_2"]["box"] = None
        result, _ = self._run(payload)
        self.assertEqual(len(result.things), 1)

    def test_out_of_range_count_is_not_silently_capped(self):
        payload = _payload(2)
        payload["answers"]["obstacle_count"]["choice"] = "7"
        with self.assertRaisesRegex(RuntimeError, "invalid obstacle response"):
            self._run(payload)

    def test_missing_credentials_do_not_make_a_network_request(self):
        with (
            mock.patch.dict("os.environ", {"JEV_API_KEY": "", "TYPESAFE_API_KEY": ""}),
            mock.patch("requests.Session.post") as post,
        ):
            with self.assertRaisesRegex(RuntimeError, "requires JEV_API_KEY"):
                JevDiscreteObstaclesPlugin().perceive(_inputs())
        post.assert_not_called()
