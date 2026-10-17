"""Capture cost, lifecycle responsiveness and conditional publication contracts."""
from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import patch

import numpy as np

from autonomy.decision_cycle.activation import DECISION_STEPS
from autonomy.decision_cycle.steps import decision_steps
from autonomy.runtime.frame_loop import FrameLoop, encode_jpeg
from autonomy.runtime.session import RunConfiguration
from implementations.decision_cycle.catalog import packaged_activation
from implementations.runtime.chase_sim.service import ChaseFrameLoop
from implementations.vehicle.chase_sim.capture import ChaseCameraCapture
from implementations.vehicle.chase_sim.session import ChasePassiveCaptureError
from tests.deterministic.autonomy.runtime.test_routes import _Target, _json, _routes
from tests.shared.cli.automation_pipeline.pipeline_fixtures import _FakeCar


def _active_routes():
    activations = {step: packaged_activation(step) for step in DECISION_STEPS}
    return _routes(decision_steps(activations), decision_activations={
        step: activation.to_payload() for step, activation in activations.items()
    })


class CaptureCloseoutTests(unittest.TestCase):
    def test_an_old_decoder_failure_cannot_stop_a_restarted_session(self):
        base, _ = _routes()
        loop = ChaseFrameLoop(car=_FakeCar(), host=base.host)
        entered, release = threading.Event(), threading.Event()

        def old_loader():
            entered.set()
            if not release.wait(2):
                raise TimeoutError("test did not release old capture")
            raise ValueError("old stream closed")

        try:
            loop.start(RunConfiguration(mode="observe_only"))
            loop.submit(None, frame_id="old", image_loader=old_loader)
            self.assertTrue(entered.wait(1))
            loop.start(RunConfiguration(mode="observe_only"))
            loop.submit(np.zeros((4, 4, 3), dtype=np.uint8), frame_id="new")
        finally:
            release.set()
            loop.wait_for_cycle()
        self.assertEqual(loop.host.run_state, "running")
        self.assertEqual(loop.latest_state.frame_id, "new")
        self.assertEqual(loop.host.run_decisions, 1)
        loop.shutdown()

    def test_atomic_identity_changes_are_rejected_in_every_mode(self):
        for mode in ("manual", "observe_only", "autonomy"):
            for field in ("scenario_id", "simulation_epoch", "control_source"):
                with self.subTest(mode=mode, field=field):
                    base, _ = _routes()
                    car = _FakeCar()
                    loop = ChaseFrameLoop(car=car, host=base.host)
                    before = {"game_id": "chase", "scenario_id": "first", "simulation_epoch": "epoch",
                              "control_source": "ws" if mode == "autonomy" else "keyboard", "unknown_fields": []}
                    car.last_passive_capture = {"session_preservation": {"before": before, "after": before}}
                    loop.start(RunConfiguration(mode=mode))
                    capture = ChaseCameraCapture("first", 1, "epoch", int(time.time() * 1000), "atomic",
                                                 lambda: np.zeros((4, 4, 3), dtype=np.uint8))
                    loop._submit_capture(capture, generation=loop._capture_generation, check_session=True)
                    loop.wait_for_cycle()
                    car.last_passive_capture["session_preservation"]["after"] = {**before, field: "changed"}
                    with self.assertRaises(ChasePassiveCaptureError):
                        loop._submit_capture(capture, generation=loop._capture_generation, check_session=True)
                    self.assertEqual(loop.frames_received, 1)
                    loop.shutdown()

    def test_only_selected_captures_decode_and_every_replacement_is_counted_once(self):
        loop, routes = _routes()
        entered, release = threading.Event(), threading.Event()
        decoded = []
        wire = encode_jpeg(np.zeros((4, 4, 3), dtype=np.uint8))

        def loader(index):
            decoded.append(index)
            if index == 1:
                entered.set()
                if not release.wait(2):
                    raise TimeoutError("test did not release selected capture")
            return np.full((4, 4, 3), index, dtype=np.uint8)

        try:
            loop.submit(None, frame_id="first", jpeg_bytes=wire, image_loader=lambda: loader(1))
            self.assertTrue(entered.wait(1))
            for index in range(2, 9):
                loop.submit(None, frame_id=str(index), jpeg_bytes=wire,
                            image_loader=lambda index=index: loader(index))
            status = loop.observation_status()
            self.assertEqual((status["frames_received"], status["frames_dropped"]), (8, 6))
            self.assertEqual(decoded, [1])
            status, headers, jpeg = routes.handle("GET", "/autonomy/camera/latest/frame.jpg")
            self.assertEqual((status, headers["X-Frame-Id"], jpeg), (200, "8", wire))
            self.assertEqual(decoded, [1], "camera publication decoded a waiting capture")
        finally:
            release.set()
            loop.wait_for_cycle()
        self.assertEqual(decoded, [1, 8])
        self.assertEqual(loop.processed_count, 2)

    def test_shutdown_rejects_arrivals_without_adding_received_or_drop_counts(self):
        loop, _ = _routes()
        loop.submit(np.zeros((4, 4, 3), dtype=np.uint8))
        loop.wait_for_cycle()
        loop.shutdown()
        self.assertIsNone(loop.submit(np.zeros((4, 4, 3), dtype=np.uint8)))
        self.assertEqual((loop.frames_received, loop.frames_dropped), (1, 0))

    def test_status_and_publications_do_not_wait_for_simulator_acquisition(self):
        entered, release = threading.Event(), threading.Event()

        class Target(_Target):
            def acquire(self):
                entered.set()
                if not release.wait(2):
                    raise TimeoutError("test did not release acquisition")

        loop, _ = _routes()
        loop.host.execution.target = Target()
        chase = ChaseFrameLoop(car=_FakeCar(), host=loop.host)
        from autonomy.runtime.routes import RuntimeRoutes
        routes = RuntimeRoutes(chase)
        errors = []

        def start():
            try:
                chase.start(RunConfiguration(mode="autonomy"))
            except Exception as exc:
                errors.append(exc)

        worker = threading.Thread(target=start, daemon=True)
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            began = time.monotonic()
            status, payload = _json(routes.handle("GET", "/autonomy/status"))
            routes.handle("GET", "/autonomy/observation/transaction")
            routes.handle("GET", "/autonomy/camera/latest")
            self.assertLess(time.monotonic() - began, 0.25)
            self.assertEqual((status, payload["session"]["status"]), (200, "starting"))
            self.assertEqual(payload["mode"], "manual")
            self.assertFalse(chase._capture_enabled)
        finally:
            release.set()
            worker.join(2)
            chase.shutdown()
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])

    def test_unchanged_transactions_do_not_encode_or_transfer_images(self):
        loop, routes = _active_routes()
        loop.submit(np.zeros((4, 4, 3), dtype=np.uint8), frame_id="first")
        loop.wait_for_cycle()
        for kind in ("observation", "decision"):
            path = f"/autonomy/{kind}/transaction"
            status, headers, body = routes.handle("GET", path)
            self.assertEqual(status, 200, body)
            with patch.object(FrameLoop, "_frame_jpeg", side_effect=AssertionError("unchanged image encoded")):
                status, again, body = routes.handle("GET", path, headers={"If-None-Match": headers["ETag"]})
            self.assertEqual((status, body), (304, b""))
            self.assertEqual(again["ETag"], headers["ETag"])
            self.assertIn("X-Read-At-Ms", again)

    def test_conditional_decision_expires_even_when_its_image_is_cached(self):
        loop, routes = _active_routes()
        loop.submit(np.zeros((4, 4, 3), dtype=np.uint8))
        loop.wait_for_cycle()
        status, headers, _ = routes.handle("GET", "/autonomy/decision/transaction")
        self.assertEqual(status, 200)
        with patch("autonomy.runtime.frame_loop.timestamp_ms", return_value=loop.latest_state.completed_at_ms + 100_000):
            status, payload = _json(routes.handle("GET", "/autonomy/decision/transaction",
                                                  headers={"If-None-Match": headers["ETag"]}))
        self.assertEqual(status, 503)
        self.assertEqual(payload["publication"]["reason"], "expired")
        self.assertIsNone(payload["image_base64"])

    def test_both_transactions_report_encode_failure_and_recover_without_a_new_cycle(self):
        loop, routes = _active_routes()
        loop.submit(np.zeros((4, 4, 3), dtype=np.uint8), frame_id="first")
        loop.wait_for_cycle()
        for kind in ("observation", "decision"):
            with patch.object(FrameLoop, "_frame_jpeg", side_effect=ValueError("bad image")):
                status, payload = _json(routes.handle("GET", f"/autonomy/{kind}/transaction"))
            self.assertEqual(status, 503)
            self.assertEqual(payload["reason"], "image_encoding_failed")
            self.assertIsNone(payload["image_base64"])
            status, payload = _json(routes.handle("GET", f"/autonomy/{kind}/transaction"))
            self.assertEqual(status, 200)
            self.assertEqual(payload["image_frame_id"], "first")
