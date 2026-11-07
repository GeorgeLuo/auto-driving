"""Image ownership and frame-loop recovery at failure boundaries."""
from __future__ import annotations

import io
import threading
import unittest
from dataclasses import replace
from unittest.mock import patch

import numpy as np
from PIL import Image

from autonomy.runtime.frame_loop import detach_image, encode_jpeg
from autonomy.runtime.session import RunConfiguration
from tests.deterministic.autonomy.runtime.test_routes import _routes


class FrameImageTests(unittest.TestCase):
    def test_camera_storage_can_change_without_mutating_a_detached_image(self):
        for image in (np.zeros((4, 4, 3), dtype=np.uint8), [[0, 0], [0, 0]]):
            with self.subTest(kind=type(image).__name__):
                detached = detach_image(image)
                image[0][0] = 200
                self.assertTrue(np.all(np.asarray(detached) == 0))
        self.assertIsNone(detach_image(None))

    def test_a_copy_failure_does_not_silently_share_camera_storage(self):
        class UncopyableImage:
            def copy(self):
                raise RuntimeError("camera buffer cannot be copied")

            def __deepcopy__(self, memo):
                return self.copy()

        with self.assertRaisesRegex(RuntimeError, "camera buffer cannot be copied"):
            detach_image(UncopyableImage())

    def test_jpeg_preserves_grayscale_and_rgb_and_ignores_extra_channels(self):
        for channels in (None, 3, 4):
            with self.subTest(channels=channels):
                if channels is None:
                    image = np.full((8, 12), 80, dtype=np.uint8)
                    expected = 80
                else:
                    image = np.zeros((8, 12, channels), dtype=np.uint8)
                    image[:, :, :3] = (200, 20, 10)
                    expected = (200, 20, 10)
                with Image.open(io.BytesIO(encode_jpeg(image))) as decoded:
                    self.assertEqual(decoded.size, (12, 8))
                    self.assertEqual(decoded.mode, "L" if channels is None else "RGB")
                    np.testing.assert_allclose(np.asarray(decoded)[0, 0], expected, atol=3)

    def test_invalid_images_report_the_input_error(self):
        with self.assertRaisesRegex(ValueError, "image_array is required"):
            encode_jpeg(None)
        for shape in ((4,), (4, 4, 2), (2, 2, 2, 3)):
            with self.subTest(shape=shape), self.assertRaisesRegex(ValueError, "unsupported image shape"):
                encode_jpeg(np.zeros(shape, dtype=np.uint8))

    def test_jpeg_encoder_failure_keeps_its_original_error(self):
        image = np.zeros((4, 4, 3), dtype=np.uint8)
        with patch.object(Image.Image, "save", side_effect=OSError("JPEG write failed")):
            with self.assertRaisesRegex(OSError, "JPEG write failed"):
                encode_jpeg(image)


class FrameLoopRecoveryTests(unittest.TestCase):
    def test_a_failed_worker_start_can_be_retried_by_the_next_capture(self):
        loop, _ = _routes()
        self.addCleanup(loop.shutdown)
        image = np.zeros((4, 4, 3), dtype=np.uint8)
        with patch.object(threading.Thread, "start", side_effect=RuntimeError("thread unavailable")):
            with self.assertRaisesRegex(RuntimeError, "thread unavailable"):
                loop.submit(image, frame_id="first")
        loop.submit(image, frame_id="second")
        loop.wait_for_cycle()
        self.assertEqual(loop.latest_state.frame_id, "second")
        self.assertEqual((loop.frames_received, loop.frames_dropped, loop.processed_count), (2, 1, 1))

    def test_invalid_step_data_publishes_an_error_for_the_current_frame(self):
        loop, _ = _routes()
        self.addCleanup(loop.shutdown)
        image = np.zeros((4, 4, 3), dtype=np.uint8)
        loop.submit(image, frame_id="first")
        loop.wait_for_cycle()
        run = loop.host.run

        def invalid_result(*args, **kwargs):
            return replace(run(*args, **kwargs), memory={"bad": object()})

        with patch.object(loop.host, "run", side_effect=invalid_result), self.assertLogs(
            "autonomy.runtime.frame_loop", level="ERROR",
        ):
            loop.submit(image, frame_id="second")
            loop.wait_for_cycle()
        report = loop.publish_latest()["report"]
        self.assertEqual(report["frame_id"], "second")
        self.assertEqual(report["status"], "error")
        self.assertIn("frame report failed", report["error"])
        self.assertTrue(all(value is None for value in report["cycle"].values()))
        self.assertEqual(report["generation_id"], loop.host.applied_decision()["generation_id"])
        self.assertEqual(loop.processed_count, 2)

    def test_an_unrecoverable_report_keeps_the_previous_publication_and_releases_the_worker(self):
        loop, _ = _routes()
        self.addCleanup(loop.shutdown)
        image = np.zeros((4, 4, 3), dtype=np.uint8)
        loop.submit(image, frame_id="first")
        loop.wait_for_cycle()
        with (
            patch.object(loop, "_capture_frame_report", side_effect=ValueError("report unavailable")),
            patch.object(loop, "_frame_completed") as completed,
            self.assertLogs("autonomy.runtime.frame_loop", level="ERROR"),
        ):
            loop.submit(image, frame_id="second")
            loop.wait_for_cycle()
        self.assertEqual(loop.publish_latest()["report"]["frame_id"], "first")
        self.assertEqual(loop.processed_count, 1)
        completed.assert_called_once_with()
        loop.submit(image, frame_id="third")
        loop.wait_for_cycle()
        self.assertEqual(loop.latest_state.frame_id, "third")
        self.assertEqual(loop.processed_count, 2)

    def test_an_old_cycle_failure_does_not_discard_a_restarted_sessions_capture(self):
        loop, _ = _routes()
        self.addCleanup(loop.shutdown)
        entered, release = threading.Event(), threading.Event()
        run = loop.host.run

        def fail_old(context, **inputs):
            if context.frame_id == "old":
                entered.set()
                if not release.wait(2):
                    raise TimeoutError("test did not release old cycle")
                raise RuntimeError("retired cycle failed")
            return run(context, **inputs)

        with patch.object(loop.host, "run", side_effect=fail_old), self.assertLogs(
            "autonomy.runtime.frame_loop", level="ERROR",
        ):
            try:
                loop.start(RunConfiguration(mode="observe_only", num_decisions=None))
                loop.submit(None, frame_id="old")
                self.assertTrue(entered.wait(1))
                loop.start(RunConfiguration(mode="observe_only", num_decisions=None))
                loop.submit(None, frame_id="new")
            finally:
                release.set()
                loop.wait_for_cycle()
        self.assertEqual(loop.latest_state.frame_id, "new")
        self.assertEqual(loop.host.run_state, "running")
        self.assertEqual(loop.host.run_decisions, 1)
