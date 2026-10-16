from __future__ import annotations

import json
import queue
import time
import unittest
from pathlib import Path
from unittest import mock

from implementations.vehicle.chase_sim.camera_stream import ChaseCameraStream
from implementations.vehicle.chase_sim.car import ChasePassiveCaptureError, ChaseSimCar
from implementations.vehicle.chase_sim.frame_identity import ChaseCaptureValidationError
from implementations.vehicle.chase_sim.metrics_ws import MetricsUiWebSocketError, MetricsUiWsClient
from tests.deterministic.implementations.vehicle.chase_frame_identity_fixtures import (
    ChaseFrameIdentityFixture, _atomic_capture, _session_debug, _session_state, _with_passive_receipt,
)
from tests.shared.implementations.runtime.wait import wait_until


def _frame(index: int = 42) -> dict:
    capture = _atomic_capture(frame_index=index)
    capture["sensor"]["image"] = {
        "contentType": "image/jpeg", "rendererId": "test-camera", "width": 2, "height": 3,
        "dataUrl": ChaseFrameIdentityFixture()._raster_data_url("JPEG", "image/jpeg"),
    }
    return {
        "subscriptionId": "test-stream", "actorId": "chaser", "cameraId": "front_camera",
        "frameIdentity": capture["frameIdentity"], "sensor": capture["sensor"],
        "playback": {"advanced": False}, "sourceTimestampUs": index * 1000,
        "publishedAtUs": index * 1000, "droppedFrameCount": 0,
    }


def _subscribed() -> dict:
    receipt = _with_passive_receipt(_atomic_capture())["passiveObservation"]["preservation"]
    return {
        "event": "subscribed", "subscriptionId": "test-stream", "cameraStream": {"supported": True},
        "playback": {"advanced": False}, "preservation": receipt, "frame": _frame(),
    }


class _Connection:
    def __init__(self, result: dict | None = None) -> None:
        self.messages: queue.Queue = queue.Queue()
        self.sent: list[dict] = []
        self.result = _subscribed() if result is None else result
        self.sock = self
        self.closed = False

    def connect(self):
        pass

    def _require_sock(self):
        return self

    def settimeout(self, _timeout):
        pass

    def send_json(self, message):
        self.sent.append(message)
        if message["type"] == "register":
            self.messages.put({"type": "ack", "payload": "registered as agent"})
        elif message["type"] == "play_camera_stream_subscribe":
            self.messages.put({"type": "play_camera_stream_result", "payload": self.result})

    def recv_json(self, *, timeout_s=None):
        message = self.messages.get(timeout=timeout_s)
        if isinstance(message, Exception):
            raise message
        return message

    def push(self, frame):
        self.messages.put({"type": "play_camera_stream_frame", "payload": frame})

    def shutdown(self, _how):
        self.messages.put(MetricsUiWebSocketError("connection closed"))

    def close(self):
        self.closed = True
        self.sock = None
        self.messages.put(MetricsUiWebSocketError("connection closed"))


class ChaseCameraStreamTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = MetricsUiWsClient("ws://example.test/ws")
        self.connection = _Connection()
        self.patch = mock.patch(
            "implementations.vehicle.chase_sim.camera_stream._WebSocketConnection",
            return_value=self.connection,
        )
        self.patch.start()
        self.addCleanup(self.patch.stop)
        state_patch = mock.patch.object(self.client, "get_state", return_value=_session_state())
        self.state = state_patch.start()
        self.addCleanup(state_patch.stop)

    def test_subscription_decodes_in_memory_and_unsubscribes_without_movement(self):
        with ChaseCameraStream(self.client) as stream:
            frame = stream.read_camera_frame()
            self.assertEqual(frame.read_id, "chase_frame_000042")
            self.assertEqual(frame.readings["front_camera"].value.shape, (3, 2, 3))
            self.assertIsNone(frame.readings["front_camera"].path)
            self.assertEqual(frame.metadata["camera_transport"], "stream")
        self.assertTrue(self.connection.closed)
        self.assertEqual([m["type"] for m in self.connection.sent], [
            "register", "play_camera_stream_subscribe", "play_camera_stream_unsubscribe",
        ])

    def test_slow_consumer_gets_only_the_newest_waiting_frame(self):
        with ChaseCameraStream(self.client) as stream:
            for index in range(43, 51):
                self.connection.push(_frame(index))
            wait_until(lambda: stream.frames_received == 9, message="stream did not drain the wire")
            frame = stream.read_camera_frame()
            self.assertEqual(frame.metadata["simulator_frame_index"], 50)
            self.assertEqual(stream.frames_replaced, 8)
            self.assertIsNone(stream.read_camera_frame(timeout_s=0.01))

    def test_waiting_in_the_slot_does_not_refresh_capture_time(self):
        with mock.patch("implementations.vehicle.chase_sim.camera_stream.time.time", return_value=1000.0) as now:
            with ChaseCameraStream(self.client) as stream:
                now.return_value = 1001.0
                frame = stream.read_camera_frame()
                self.assertLessEqual(frame.readings["front_camera"].captured_at_ms, 1000000)
                self.assertEqual(frame.completed_at_ms, 1001000)

    def test_delayed_wire_delivery_retains_source_frame_age(self):
        with mock.patch("implementations.vehicle.chase_sim.camera_stream.time.monotonic", return_value=1000.0) as monotonic, \
                mock.patch("implementations.vehicle.chase_sim.camera_stream.time.time", return_value=1000.0) as wall:
            with ChaseCameraStream(self.client) as stream:
                stream.read_camera_frame()
                monotonic.return_value = wall.return_value = 1001.0
                self.connection.push(_frame(43))
                frame = stream.read_camera_frame(timeout_s=1)
                self.assertEqual(frame.readings["front_camera"].captured_at_ms, 1000001)
                self.assertEqual(frame.completed_at_ms, 1001000)

    def test_source_clock_jump_is_rejected(self):
        with ChaseCameraStream(self.client) as stream:
            stream.read_camera_frame()
            frame = _frame(43)
            frame["sourceTimestampUs"] = frame["publishedAtUs"] = 1000000000
            self.connection.push(frame)
            with self.assertRaises(ChaseCaptureValidationError):
                stream.read_camera_frame(timeout_s=1)

    def test_invalid_startup_receipt_is_rejected_and_connection_closed(self):
        self.connection.result["preservation"]["after"]["simulationEpoch"] = "changed"
        with self.assertRaises(ChasePassiveCaptureError):
            with ChaseCameraStream(self.client):
                self.fail("invalid subscription accepted")
        self.assertTrue(self.connection.closed)

    def test_stream_rejects_identity_and_timestamp_regressions(self):
        mutations = [
            ("subscriptionId", "other"), ("actorId", "evader"),
            ("cameraId", "other"), ("sourceTimestampUs", True),
            ("publishedAtUs", 0), ("frameIdentity", {"gameId": "chase", "frameIndex": 43,
                                                       "simulationEpoch": "other"}),
        ]
        for key, value in mutations:
            with self.subTest(key=key):
                self.connection = _Connection()
                with mock.patch("implementations.vehicle.chase_sim.camera_stream._WebSocketConnection",
                                return_value=self.connection):
                    with ChaseCameraStream(self.client) as stream:
                        self.assertIsNotNone(stream.read_camera_frame())
                        frame = _frame(43)
                        frame[key] = value
                        self.connection.push(frame)
                        with self.assertRaises(ChaseCaptureValidationError):
                            stream.read_camera_frame(timeout_s=1)

    def test_stream_rejects_a_repeated_frame_index(self):
        with ChaseCameraStream(self.client) as stream:
            stream.read_camera_frame()
            self.connection.push(_frame(42))
            with self.assertRaises(ChaseCaptureValidationError):
                stream.read_camera_frame(timeout_s=1)

    def test_control_source_changes_are_rejected_before_perception(self):
        self.state.return_value = _session_state(control_source="keyboard")
        with ChaseCameraStream(self.client) as stream:
            with self.assertRaises(ChasePassiveCaptureError) as raised:
                stream.read_camera_frame()
        self.assertIn("control_source", raised.exception.details["session_preservation"]["changed_fields"])

    def test_ended_stream_does_not_deliver_an_old_buffered_frame(self):
        with ChaseCameraStream(self.client) as stream:
            stream.read_camera_frame()
            self.connection.messages.put({"type": "play_camera_stream_result", "payload": {
                "event": "ended", "reason": {"code": "frontend_disconnected"},
            }})
            with self.assertRaises(MetricsUiWebSocketError):
                stream.read_camera_frame(timeout_s=1)

    def test_idle_playing_stream_fails_instead_of_waiting_forever(self):
        with ChaseCameraStream(self.client) as stream:
            stream.read_camera_frame()
            with mock.patch("implementations.vehicle.chase_sim.camera_stream.time.monotonic",
                            return_value=time.monotonic() + 6), \
                    mock.patch.object(self.client, "get_play_debug", return_value={"phase": "running"}):
                with self.assertRaises(MetricsUiWebSocketError) as raised:
                    stream.read_camera_frame(timeout_s=0.001)
        self.assertEqual(raised.exception.code, "frontend_unresponsive")

    def test_game_pause_keeps_an_idle_subscription_open(self):
        with ChaseCameraStream(self.client) as stream:
            stream.read_camera_frame()
            with mock.patch("implementations.vehicle.chase_sim.camera_stream.time.monotonic",
                            return_value=time.monotonic() + 6), \
                    mock.patch.object(self.client, "get_play_debug",
                                      return_value={"phase": "paused-before-actions"}) as debug:
                self.assertIsNone(stream.read_camera_frame(timeout_s=0.001))
                debug.assert_called_once()
            self.assertFalse(self.connection.closed)


class ChaseCameraMemoryTests(unittest.TestCase):
    def test_atomic_camera_decodes_once_and_retains_serializable_receipts(self):
        car = ChaseSimCar(ws_url="ws://example.test/ws")
        capture = _with_passive_receipt(_atomic_capture())
        with mock.patch.object(car.client, "get_state", return_value=_session_state()), \
                mock.patch.object(car.client, "get_play_debug", return_value=_session_debug()) as debug, \
                mock.patch.object(car.client, "play_game_query", return_value=capture) as query, \
                mock.patch.object(Path, "write_bytes", side_effect=AssertionError("camera wrote a file")):
            first = car.read_camera_frame()
            second = car.read_camera_frame()
        self.assertEqual(first.readings["front_camera"].value.shape, (1, 1, 3))
        self.assertIsNone(second.readings["front_camera"].path)
        self.assertEqual(debug.call_count, 1)
        self.assertEqual(query.call_count, 2)
        self.assertNotIn("image_array", car.last_passive_capture["sensor"]["image"])
        json.dumps(car.last_passive_capture)

    def test_atomic_capture_rejects_a_receipt_downgrade_after_discovery(self):
        car = ChaseSimCar(ws_url="ws://example.test/ws")
        with mock.patch.object(car.client, "get_state", return_value=_session_state()), \
                mock.patch.object(car.client, "get_play_debug", return_value=_session_debug()), \
                mock.patch.object(car.client, "play_game_query", side_effect=[
                    _with_passive_receipt(_atomic_capture()), _atomic_capture(frame_index=43),
                ]):
            car.read_camera_frame()
            with self.assertRaises(ChasePassiveCaptureError):
                car.read_camera_frame()

    def test_older_frontend_without_stream_capability_keeps_atomic_capture(self):
        car = ChaseSimCar(ws_url="ws://example.test/ws")
        with mock.patch.object(car.client, "command", return_value=mock.Mock(payload={"protocol": {}})):
            self.assertIsNone(car.camera_stream())


if __name__ == "__main__":
    unittest.main()
