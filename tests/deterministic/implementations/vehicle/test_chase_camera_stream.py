from __future__ import annotations

import queue
import threading
import time
import unittest
from contextlib import contextmanager
from unittest import mock

from implementations.vehicle.chase_sim import ChasePassiveCaptureError, ChaseSimCar
from implementations.vehicle.chase_sim.camera_stream import ChaseCameraStream
from implementations.vehicle.chase_sim.frame_identity import ChaseCaptureValidationError
from implementations.vehicle.chase_sim.metrics_ws import MetricsUiCommandResponse, MetricsUiWebSocketError, MetricsUiWsClient
from tests.deterministic.implementations.vehicle.chase_frame_identity_fixtures import (
    _Connection, _frame, _session_state,
)
from tests.shared.implementations.runtime.wait import wait_until


@contextmanager
def _capturing(client):
    captures = queue.Queue()
    with ChaseCameraStream(client) as stream:
        stream.set_consumer(captures.put)
        yield stream, captures


def _next_capture(stream, captures, timeout_s=1):
    deadline = time.monotonic() + timeout_s
    while True:
        stream.check_progress()
        try:
            return captures.get(timeout=min(.01, max(.001, deadline-time.monotonic())))
        except queue.Empty:
            if time.monotonic() >= deadline:
                raise TimeoutError("capture was not delivered")


class ChaseCameraStreamTests(unittest.TestCase):
    def setUp(self):
        self.client = MetricsUiWsClient("ws://example.test/ws")
        self.connection = _Connection()
        self.patch = mock.patch("implementations.vehicle.chase_sim.camera_stream.MetricsUiConnection",
                                return_value=self.connection)
        self.factory = self.patch.start()
        self.addCleanup(self.patch.stop)
        self.state = self.connection.state

    def test_subscription_delivers_one_buffered_capture_then_unsubscribes_without_movement(self):
        with _capturing(self.client) as (stream, captures):
            capture = _next_capture(stream, captures)
            self.assertEqual(capture.frame_id, "chase_frame_000042")
            self.assertEqual(capture.load_image().shape, (3, 2, 3))
            self.assertTrue(captures.empty())
            self.assertTrue(capture.jpeg_bytes.startswith(b"\xff\xd8"))
        self.assertTrue(self.connection.closed)
        self.assertEqual([m["type"] for m in self.connection.sent], [
            "register", "play_camera_stream_subscribe", "get_state", "get_state", "play_camera_stream_unsubscribe",
        ])

    def test_every_capture_after_startup_reaches_the_shared_slot_in_order(self):
        with _capturing(self.client) as (stream, captures):
            for index in range(43, 51):
                self.connection.push(_frame(index))
            wait_until(lambda: captures.qsize() == 9, message="stream did not drain the wire")
            self.assertEqual([captures.get_nowait().frame_index for _ in range(9)], list(range(42, 51)))
            self.assertIsNone(stream._latest)

    def test_waiting_for_consumption_does_not_refresh_capture_time(self):
        with mock.patch("implementations.vehicle.chase_sim.camera_stream.time.time", return_value=1000.) as wall:
            with _capturing(self.client) as (stream, captures):
                wall.return_value = 1001.
                capture = _next_capture(stream, captures)
                self.assertLessEqual(capture.captured_at_ms, 1000000)

    def test_delayed_wire_delivery_retains_source_frame_age(self):
        with mock.patch("implementations.vehicle.chase_sim.camera_stream.time.monotonic", return_value=1000.) as clock, \
                mock.patch("implementations.vehicle.chase_sim.camera_stream.time.time", return_value=1000.) as wall:
            with _capturing(self.client) as (stream, captures):
                _next_capture(stream, captures)
                clock.return_value = wall.return_value = 1001.
                self.connection.push(_frame(43))
                capture = _next_capture(stream, captures)
                self.assertEqual(capture.captured_at_ms, 1000001)

    def test_raw_consumer_defers_decode_and_state_reads_until_selection(self):
        from PIL.JpegImagePlugin import JpegImageFile
        frames = [_frame(index) for index in range(43, 51)]
        with mock.patch.object(JpegImageFile, "load", autospec=True, side_effect=JpegImageFile.load) as decode:
            with _capturing(self.client) as (stream, captures):
                for frame in frames:
                    self.connection.push(frame)
                wait_until(lambda: captures.qsize() == 9, message="raw captures not delivered")
                self.assertEqual((decode.call_count, self.state.call_count), (0, 1))
                latest = [captures.get_nowait() for _ in range(9)][-1]
                self.assertEqual(latest.load_image().shape, (3, 2, 3))
                self.assertGreater(decode.call_count, 0)
                self.assertEqual(self.state.call_count, 2)

    def test_invalid_receipt_fails_before_delivering_a_capture(self):
        self.connection.result["preservation"]["after"]["simulationEpoch"] = "changed"
        with self.assertRaises(ChasePassiveCaptureError), _capturing(self.client):
            self.fail("invalid receipt accepted")
        self.assertTrue(self.connection.closed)

    def test_malformed_frames_and_source_clock_jumps_are_fatal(self):
        for key, value in [("subscriptionId", "other"), ("actorId", "evader"), ("cameraId", "other"),
                           ("sourceTimestampUs", True), ("publishedAtUs", 0),
                           ("sourceTimestampUs", 1000000000)]:
            with self.subTest(key=key, value=value):
                connection = _Connection()
                with mock.patch("implementations.vehicle.chase_sim.camera_stream.MetricsUiConnection", return_value=connection):
                    with _capturing(self.client) as (stream, captures):
                        _next_capture(stream, captures)
                        frame = _frame(43); frame[key] = value
                        if value == 1000000000:
                            frame["publishedAtUs"] = value
                        connection.push(frame)
                        with self.assertRaises(ChaseCaptureValidationError):
                            _next_capture(stream, captures)

    def test_repeated_frame_identity_is_rejected(self):
        with _capturing(self.client) as (stream, captures):
            _next_capture(stream, captures)
            self.connection.push(_frame(42))
            with self.assertRaises(ChaseCaptureValidationError):
                _next_capture(stream, captures)

    def test_control_source_changes_fail_before_selected_pixels_decode(self):
        with _capturing(self.client) as (stream, captures):
            capture = _next_capture(stream, captures)
            self.state.return_value = _session_state(control_source="keyboard")
            with self.assertRaises(ChasePassiveCaptureError) as raised:
                capture.load_image()
            self.assertIn("control_source", raised.exception.details["session_preservation"]["changed_fields"])

    def test_ended_stream_rejects_a_retained_capture(self):
        with _capturing(self.client) as (stream, captures):
            capture = _next_capture(stream, captures)
            self.connection.messages.put({"type": "play_camera_stream_result", "payload": {"event": "ended"}})
            wait_until(lambda: stream._error is not None, message="ended event not received")
            with self.assertRaises(MetricsUiWebSocketError):
                capture.load_image()

    def test_idle_playing_stream_fails_but_paused_game_keeps_subscription(self):
        for phase in ["running", "paused-before-actions"]:
            self.connection = _Connection()
            with self.subTest(phase=phase), mock.patch(
                "implementations.vehicle.chase_sim.camera_stream.MetricsUiConnection", return_value=self.connection,
            ), _capturing(self.client) as (stream, captures):
                _next_capture(stream, captures)
                with mock.patch("implementations.vehicle.chase_sim.camera_stream.time.monotonic", return_value=time.monotonic()+6), \
                        mock.patch.object(self.client, "get_play_debug", return_value={"phase": phase}):
                    if phase == "running":
                        with self.assertRaises(MetricsUiWebSocketError):stream.check_progress()
                    else:stream.check_progress()

    def test_state_baseline_and_selected_checks_use_one_connection(self):
        with mock.patch.object(self.client, "get_state", side_effect=AssertionError("new connection")):
            with _capturing(self.client) as (stream, captures):
                _next_capture(stream, captures).load_image()
                for index in range(43, 46):
                    self.connection.push(_frame(index))
                    _next_capture(stream, captures).load_image()
        self.factory.assert_called_once_with(self.client.url, timeout_s=self.client.timeout_s)

    def test_missing_or_malformed_sidebar_fails_during_subscription(self):
        for state in ({}, []):
            connection = _Connection(); connection.state.return_value = state
            with self.subTest(state=state), mock.patch(
                "implementations.vehicle.chase_sim.camera_stream.MetricsUiConnection", return_value=connection,
            ), self.assertRaises((ChasePassiveCaptureError, MetricsUiWebSocketError)), _capturing(self.client):
                self.fail("invalid state accepted")

    def test_unsolicited_state_does_not_satisfy_a_request(self):
        self.client.timeout_s = .05
        self.connection.answer_state = False
        self.connection.messages.put({"type": "state_update", "payload": _session_state()})
        self.connection.messages.put({"type": "state_update", "request_id": "wrong", "payload": _session_state()})
        with self.assertRaisesRegex(MetricsUiWebSocketError, "timed out"), _capturing(self.client):
            self.fail("unrelated reply accepted")

    def test_close_interrupts_a_selected_capture_waiting_for_state(self):
        with _capturing(self.client) as (stream, captures):
            capture = _next_capture(stream, captures)
            self.connection.answer_state = False
            self.connection.state_requested.clear()
            errors = []
            def load():
                try:capture.load_image()
                except Exception as exc:errors.append(exc)
            worker = threading.Thread(target=load); worker.start()
            self.assertTrue(self.connection.state_requested.wait(1))
            stream.close(); worker.join(1)
            self.assertFalse(worker.is_alive())
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], MetricsUiWebSocketError)

    def test_stream_requires_advertised_capability_and_propagates_usage_errors(self):
        car = ChaseSimCar(ws_url="ws://example.test/ws")
        for payload in ({}, {"protocol": {"cameraStream": {"supported": False}}}):
            with self.subTest(payload=payload), mock.patch.object(car.client, "command", return_value=MetricsUiCommandResponse({"type": "play_game_usage", "payload": payload}, 0.)):
                with self.assertRaisesRegex(ChasePassiveCaptureError, "update Metrics UI"):
                    car.camera_stream()
        for error in [MetricsUiWebSocketError("unsupported", code="unsupported_command"), TimeoutError("offline")]:
            with self.subTest(error=error), mock.patch.object(car.client, "command", side_effect=error):
                with self.assertRaises(type(error)):car.camera_stream()
