from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorReadRequest
from implementations.vehicle.chase_sim.car import ChaseSimCar
from implementations.vehicle.chase_sim.frame_identity import (
    align_candidate_with_shadow,
    format_chase_frame_id,
    sanitize_chase_shadow_reference,
    score_shadow_alignment_batch,
    simulator_frame_index_from_snapshot,
)


class ChaseFrameIdentityTests(unittest.TestCase):
    def test_format_and_sanitize_shadow_reference(self) -> None:
        self.assertEqual(format_chase_frame_id(42), "chase_frame_000042")
        debug = {
            "gameId": "chase",
            "frameIndex": 42,
            "scenario": "chaser-depth-obstacles",
            "actions": {
                "chaserInput": {"source": "builtin", "steering": 0.1},
                "chaserAction": {"source": "builtin"},
            },
            "map": {"walls": [{"x": 1}]},  # privileged — must not be copied
        }
        shadow = sanitize_chase_shadow_reference(debug)
        assert shadow is not None
        self.assertTrue(shadow["evaluator_only"])
        self.assertEqual(shadow["simulator_frame_index"], 42)
        self.assertEqual(shadow["frame_id"], "chase_frame_000042")
        self.assertEqual(shadow["chaser_control_source"], "builtin")
        self.assertNotIn("map", shadow)
        self.assertNotIn("walls", str(shadow))

    def test_align_and_batch_score(self) -> None:
        ok = align_candidate_with_shadow(
            candidate_frame_index=7,
            shadow_reference={"simulator_frame_index": 7},
        )
        self.assertTrue(ok["aligned"])
        bad = align_candidate_with_shadow(
            candidate_frame_index=7,
            shadow_reference={"simulator_frame_index": 8},
        )
        self.assertFalse(bad["aligned"])

        frames = [
            {
                "frame_id": "chase_frame_000010",
                "simulator_frame_index": 10,
                "shadow_reference": {"simulator_frame_index": 10},
            },
            {
                "frame_id": "chase_frame_000011",
                "simulator_frame_index": 11,
                "shadow_reference": {"simulator_frame_index": 11},
            },
        ]
        score = score_shadow_alignment_batch(frames, min_frames=2)
        self.assertTrue(score["passed"], score)

        stale = [
            {
                "frame_id": "a",
                "simulator_frame_index": 1,
                "shadow_reference": {"simulator_frame_index": 1},
            },
            {
                "frame_id": "b",
                "simulator_frame_index": 1,
                "shadow_reference": {"simulator_frame_index": 1},
            },
        ]
        self.assertFalse(score_shadow_alignment_batch(stale, min_frames=2)["passed"])

    def test_read_sensors_preserves_simulator_frame_index(self) -> None:
        car = ChaseSimCar(ws_url="ws://example.test/ws", timeout_s=0.2)
        debug = {
            "gameId": "chase",
            "frameIndex": 123,
            "actions": {"chaserInput": {"source": "builtin", "throttle": 0.0}},
        }
        front = {
            "image": {
                "dataUrl": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
            },
            "width": 1,
            "height": 1,
            "frameIndex": 123,
        }

        with mock.patch.object(car, "_wait_for_play_debug", return_value=debug), mock.patch.object(
            car.client, "get_play_front_view_snapshot", return_value=front
        ):
            with unittest.mock.patch("pathlib.Path.write_bytes"), unittest.mock.patch(
                "pathlib.Path.mkdir"
            ), unittest.mock.patch("pathlib.Path.exists", return_value=True), unittest.mock.patch(
                "pathlib.Path.stat"
            ) as stat:
                stat.return_value.st_size = 68
                snapshot = car.read_sensors(
                    SensorReadRequest(
                        output_dir=Path("/tmp/chase-frame-id-test"),
                        read_id="provisional",
                        image_extension="png",
                        front_camera_endpoint="play-front-view-snapshot",
                    )
                )

        self.assertEqual(car.last_simulator_frame_index, 123)
        self.assertEqual(
            simulator_frame_index_from_snapshot(snapshot),
            123,
        )
        reading = snapshot.readings[FRONT_CAMERA_SENSOR_ID]
        self.assertEqual(reading.metadata.get("simulator_frame_index"), 123)
        self.assertEqual(reading.metadata.get("frame_id"), "chase_frame_000123")
        self.assertIsNotNone(car.last_capture_shadow_reference)
        assert car.last_capture_shadow_reference is not None
        self.assertEqual(car.last_capture_shadow_reference["simulator_frame_index"], 123)
        # Shadow stays on the car attribute, not on the sensor snapshot metadata.
        self.assertNotIn("shadow_reference", snapshot.metadata)


if __name__ == "__main__":
    unittest.main(verbosity=2)
