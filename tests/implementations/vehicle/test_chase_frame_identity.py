from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorReadRequest
from implementations.vehicle.chase_sim.car import ChaseSimCar
from implementations.vehicle.chase_sim.frame_identity import (
    align_candidate_with_shadow,
    format_chase_frame_id,
    frame_indices_strictly_increasing,
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

    def test_sanitize_refuses_relabel_to_different_frame(self) -> None:
        debug = {"gameId": "chase", "frameIndex": 10, "actions": {}}
        self.assertIsNone(
            sanitize_chase_shadow_reference(debug, require_frame_index=29)
        )
        ok = sanitize_chase_shadow_reference(debug, require_frame_index=10)
        assert ok is not None
        self.assertEqual(ok["simulator_frame_index"], 10)

    def test_align_and_batch_score_requires_strictly_increasing(self) -> None:
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
                "shadow_reference": {
                    "simulator_frame_index": 10,
                    "game_id": "chase",
                    "scenario": "chaser-depth-obstacles",
                },
            },
            {
                "frame_id": "chase_frame_000011",
                "simulator_frame_index": 11,
                "shadow_reference": {
                    "simulator_frame_index": 11,
                    "game_id": "chase",
                    "scenario": "chaser-depth-obstacles",
                },
            },
        ]
        score = score_shadow_alignment_batch(frames, min_frames=2)
        self.assertTrue(score["passed"], score)

        reversed_frames = [
            {
                "frame_id": "a",
                "simulator_frame_index": 11,
                "shadow_reference": {"simulator_frame_index": 11, "game_id": "chase"},
            },
            {
                "frame_id": "b",
                "simulator_frame_index": 10,
                "shadow_reference": {"simulator_frame_index": 10, "game_id": "chase"},
            },
        ]
        reversed_score = score_shadow_alignment_batch(reversed_frames, min_frames=2)
        self.assertFalse(reversed_score["passed"])
        self.assertFalse(reversed_score["advancing_simulator_frames"])
        self.assertFalse(frame_indices_strictly_increasing([11, 10]))
        self.assertTrue(frame_indices_strictly_increasing([10, 11, 15]))

    def test_read_sensors_pairs_exact_debug_and_camera_identity(self) -> None:
        car = ChaseSimCar(ws_url="ws://example.test/ws", timeout_s=0.5)
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

        with mock.patch.object(
            car, "_read_play_debug_best_effort", return_value=debug
        ), mock.patch.object(
            car.client, "get_play_front_view_snapshot", return_value=front
        ):
            with mock.patch("pathlib.Path.write_bytes"), mock.patch(
                "pathlib.Path.mkdir"
            ), mock.patch("pathlib.Path.exists", return_value=True), mock.patch(
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
        self.assertEqual(simulator_frame_index_from_snapshot(snapshot), 123)
        reading = snapshot.readings[FRONT_CAMERA_SENSOR_ID]
        self.assertEqual(reading.metadata.get("simulator_frame_index"), 123)
        self.assertEqual(reading.metadata.get("frame_id"), "chase_frame_000123")
        self.assertIsNotNone(car.last_capture_shadow_reference)
        assert car.last_capture_shadow_reference is not None
        self.assertEqual(car.last_capture_shadow_reference["simulator_frame_index"], 123)
        self.assertNotIn("shadow_reference", snapshot.metadata)

    def test_capture_retries_when_debug_advances_during_camera_fetch(self) -> None:
        car = ChaseSimCar(ws_url="ws://example.test/ws", timeout_s=2.0)
        debug_calls = {"n": 0}
        fronts = [
            {"image": {"dataUrl": "data:image/png;base64,aa"}, "frameIndex": 20},
            {
                "image": {
                    "dataUrl": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
                },
                "frameIndex": 21,
            },
        ]

        def fake_debug() -> dict:
            # Pair 1: before=10 after=20 (advance during camera) → reject
            # Pair 2: before=21 after=21 → accept
            n = debug_calls["n"]
            debug_calls["n"] += 1
            if n == 0:
                return {"gameId": "chase", "frameIndex": 10, "actions": {}}
            if n == 1:
                return {"gameId": "chase", "frameIndex": 20, "actions": {}}
            return {
                "gameId": "chase",
                "frameIndex": 21,
                "actions": {"chaserInput": {"source": "builtin"}},
            }

        front_calls = {"n": 0}

        def fake_front(**_kwargs) -> dict:
            idx = min(front_calls["n"], len(fronts) - 1)
            front_calls["n"] += 1
            return fronts[idx]

        with mock.patch.object(
            car, "_read_play_debug_best_effort", side_effect=fake_debug
        ), mock.patch.object(
            car.client, "get_play_front_view_snapshot", side_effect=fake_front
        ):
            with mock.patch("pathlib.Path.write_bytes"), mock.patch(
                "pathlib.Path.mkdir"
            ), mock.patch("pathlib.Path.exists", return_value=True), mock.patch(
                "pathlib.Path.stat"
            ) as stat:
                stat.return_value.st_size = 68
                snapshot = car.read_sensors(
                    SensorReadRequest(
                        output_dir=Path("/tmp/chase-frame-id-retry"),
                        read_id="provisional",
                        image_extension="png",
                    )
                )

        self.assertEqual(car.last_simulator_frame_index, 21)
        self.assertEqual(simulator_frame_index_from_snapshot(snapshot), 21)
        assert car.last_capture_shadow_reference is not None
        self.assertEqual(car.last_capture_shadow_reference["simulator_frame_index"], 21)
        self.assertGreaterEqual(front_calls["n"], 2)

    def test_capture_fails_closed_when_camera_index_mismatches_debug(self) -> None:
        car = ChaseSimCar(ws_url="ws://example.test/ws", timeout_s=0.3)
        debug = {"gameId": "chase", "frameIndex": 10, "actions": {}}
        front = {
            "image": {
                "dataUrl": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
            },
            "frameIndex": 29,  # live probe style mismatch
        }
        with mock.patch.object(
            car, "_read_play_debug_best_effort", return_value=debug
        ), mock.patch.object(
            car.client, "get_play_front_view_snapshot", return_value=front
        ):
            with mock.patch("pathlib.Path.mkdir"), self.assertRaises(TimeoutError) as ctx:
                car.read_sensors(
                    SensorReadRequest(
                        output_dir=Path("/tmp/chase-frame-id-mismatch"),
                        read_id="provisional",
                        image_extension="png",
                    )
                )
        self.assertIn("frameIndex", str(ctx.exception))
        self.assertIsNone(car.last_capture_shadow_reference)


if __name__ == "__main__":
    unittest.main(verbosity=2)
