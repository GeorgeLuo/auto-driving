from __future__ import annotations
import tempfile
import unittest
import base64
from pathlib import Path
from unittest import mock
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorReadRequest
from implementations.vehicle.chase_sim.car import (
    CHASE_ATOMIC_EVALUATION_QUERY,
    CHASE_PASSIVE_CAMERA_ID,
    ChaseSimCar,
)
from implementations.vehicle.chase_sim.frame_identity import (
    simulator_epoch_from_snapshot,
    simulator_frame_index_from_snapshot,
)
from tests.implementations.vehicle.chase_frame_identity_fixtures import (
    ChaseFrameIdentityFixture,
    _PNG_DATA_URL,
    _atomic_capture,
    _session_debug,
    _session_state,
)


class ChaseFrameIdentityTests(ChaseFrameIdentityFixture, unittest.TestCase):
    def test_read_sensors_uses_one_atomic_query_and_keeps_shadow_outside_snapshot(
        self,
    ) -> None:
        car = ChaseSimCar(ws_url="ws://example.test/ws", timeout_s=0.5)
        capture = _atomic_capture(frame_index=123)

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            car.client,
            "play_game_query",
            return_value=capture,
        ) as query, mock.patch.object(
            car.client,
            "get_state",
            side_effect=[_session_state(), _session_state()],
        ) as get_state, mock.patch.object(
            car.client,
            "get_play_debug",
            side_effect=[_session_debug(), _session_debug()],
        ) as get_debug, mock.patch.object(
            car.client,
            "get_play_front_view_snapshot",
            side_effect=AssertionError(
                "capture must not use the sequential snapshot path"
            ),
        ):
            snapshot = car.read_sensors(
                SensorReadRequest(
                    output_dir=Path(tmp),
                    read_id="atomic",
                    image_extension="png",
                    front_camera_endpoint=CHASE_ATOMIC_EVALUATION_QUERY,
                )
            )
            image_exists = Path(
                snapshot.readings[FRONT_CAMERA_SENSOR_ID].path or ""
            ).is_file()
            image_path = Path(snapshot.readings[FRONT_CAMERA_SENSOR_ID].path or "")
            image_bytes = image_path.read_bytes()

        query.assert_called_once_with(
            CHASE_ATOMIC_EVALUATION_QUERY,
            {
                "actorId": "chaser",
                "cameraId": CHASE_PASSIVE_CAMERA_ID,
                "width": 640,
                "height": 480,
            },
            timeout_s=mock.ANY,
        )
        self.assertEqual(get_state.call_count, 2)
        self.assertEqual(get_debug.call_count, 2)
        self.assertEqual(car.last_simulator_frame_index, 123)
        self.assertEqual(simulator_frame_index_from_snapshot(snapshot), 123)
        self.assertEqual(simulator_epoch_from_snapshot(snapshot), "chase-run:test")
        reading = snapshot.readings[FRONT_CAMERA_SENSOR_ID]
        self.assertEqual(
            reading.metadata["identity_pairing"], "atomic_evaluation_capture"
        )
        self.assertEqual(reading.metadata["simulation_epoch"], "chase-run:test")
        self.assertTrue(image_exists)
        self.assertEqual(
            image_bytes,
            base64.b64decode(_PNG_DATA_URL.split(",", 1)[1]),
        )
        self.assertEqual(
            snapshot.readings[FRONT_CAMERA_SENSOR_ID].metadata["content_type"],
            "image/png",
        )
        self.assertNotIn("shadow_reference", snapshot.metadata)
        self.assertNotIn("visibleWallCount", str(snapshot.to_dict()))
        self.assertNotIn("actor-control-reference", str(snapshot.to_dict()))
        self.assertEqual(
            snapshot.readings[FRONT_CAMERA_SENSOR_ID].metadata["evaluator_reference"][
                "status"
            ],
            "available",
        )

    def test_malformed_evaluator_reference_does_not_block_sensor_capture(self) -> None:
        car = ChaseSimCar(ws_url="ws://example.test/ws", timeout_s=0.3)
        malformed = _atomic_capture(frame_index=10, action_frame_index=11)
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            car.client,
            "play_game_query",
            return_value=malformed,
        ), mock.patch.object(
            car.client,
            "get_state",
            side_effect=[_session_state(), _session_state()],
        ), mock.patch.object(
            car.client,
            "get_play_debug",
            side_effect=[_session_debug(), _session_debug()],
        ):
            snapshot = car.read_sensors(
                SensorReadRequest(
                    output_dir=Path(tmp),
                    read_id="malformed-reference",
                    image_extension="png",
                )
            )
        self.assertIsNone(car.last_capture_shadow_reference)
        self.assertEqual(car.last_simulator_frame_index, 10)
        self.assertEqual(car.last_evaluator_reference["status"], "invalid")
        self.assertEqual(
            car.last_evaluator_reference["path"],
            "evaluator.reference.actionFrameIndex",
        )
        self.assertIn(FRONT_CAMERA_SENSOR_ID, snapshot.readings)

    def test_missing_evaluator_reference_does_not_block_sensor_capture(self) -> None:
        car = ChaseSimCar(ws_url="ws://example.test/ws", timeout_s=0.3)
        capture = _atomic_capture()
        capture["evaluator"].pop("reference")
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            car.client,
            "play_game_query",
            return_value=capture,
        ), mock.patch.object(
            car.client,
            "get_state",
            side_effect=[_session_state(), _session_state()],
        ), mock.patch.object(
            car.client,
            "get_play_debug",
            side_effect=[_session_debug(), _session_debug()],
        ):
            snapshot = car.read_sensors(
                SensorReadRequest(
                    output_dir=Path(tmp),
                    read_id="missing-reference",
                    image_extension="png",
                )
            )

        self.assertIsNone(car.last_capture_shadow_reference)
        self.assertEqual(car.last_evaluator_reference["status"], "unavailable")
        self.assertEqual(
            snapshot.readings[FRONT_CAMERA_SENSOR_ID].metadata["evaluator_reference"],
            {
                "status": "unavailable",
                "reason": "reference_missing",
                "path": "evaluator.reference",
            },
        )
