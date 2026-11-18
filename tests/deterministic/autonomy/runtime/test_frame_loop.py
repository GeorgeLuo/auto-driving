"""Frame JPEG encoding and frame-loop recovery at failure boundaries."""
from __future__ import annotations

import io
import threading
import unittest
from dataclasses import replace
from unittest.mock import patch

import numpy as np
from PIL import Image

from autonomy.decision_cycle.steps import decision_steps
from autonomy.runtime.cycle_host import AutonomyCycleHost
from autonomy.runtime.frame_loop import FrameLoop, FramePublication, encode_jpeg
from autonomy.runtime.routes import TELEMETRY_SCHEMA
from autonomy.runtime.session import RunConfiguration
from cli.automa_cli.hosts.publications.frame_publication import read_frame_publication
from cli.automa_cli.hosts.publications.host_telemetry import join_host_telemetry_to_decision
from implementations.runtime.picar.host_telemetry import HOST_TELEMETRY_LIMITS
from tests.deterministic.autonomy.runtime.test_routes import _Target, _routes


class FrameImageTests(unittest.TestCase):
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
        entered, release = threading.Event(), threading.Event()

        def fail_old(context):
            if context.frame_id == "old":
                entered.set()
                if not release.wait(2):
                    raise TimeoutError("test did not release old cycle")
                raise RuntimeError("retired cycle failed")
            return None

        loop, _ = _routes(replace(decision_steps(), perception=fail_old))
        self.addCleanup(loop.shutdown)
        with self.assertLogs("autonomy.runtime.frame_loop", level="ERROR"):
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

    def test_a_64_character_host_name_publishes_a_frame_the_cli_reads(self):
        host_name = "h" * 64
        loop = FrameLoop(
            host=AutonomyCycleHost(steps=decision_steps(), target=_Target()),
            host_name=host_name,
            runtime="chase_sim",
            run_id="chase-run-long-name",
        )
        self.addCleanup(loop.shutdown)
        loop.submit(np.zeros((4, 4, 3), dtype=np.uint8), frame_id="long-host-frame")
        loop.wait_for_cycle()
        report = read_frame_publication(
            FramePublication.from_dict(loop.publish_latest()), host_name=host_name,
        )
        self.assertEqual(report.values.source_id, host_name)
        completed_at_ms = report.published_at_ms
        joined = join_host_telemetry_to_decision(
            {
                "schema": TELEMETRY_SCHEMA,
                "status": "healthy",
                "host_name": report.host_name,
                "source_id": report.values.source_id,
                "run_id": report.run_id,
                "generation_id": report.generation_id,
                "source_frame": {
                    "frame_id": report.frame_id,
                    "frame_index": report.frame_index,
                    "captured_at_ms": report.timestamp_ms,
                    "completed_at_ms": completed_at_ms,
                },
                "host_tick": {
                    "sequence": 1,
                    "observed_at_ms": completed_at_ms,
                    "published_at_ms": completed_at_ms,
                    "source_age_ms": 0,
                    "gap_since_previous_ms": None,
                    "skipped_since_previous": None,
                },
                "mode": "user",
                "user_input": {"steering": 0.0, "throttle": 0.0},
                "pilot_output": {"steering": 0.0, "throttle": 0.0},
                "host_selected_output": {"steering": 0.0, "throttle": 0.0},
                "application": {
                    "boundary": "post_drive_mode_pre_drivetrain",
                    "actuator_feedback": "unavailable",
                },
                "limits": dict(HOST_TELEMETRY_LIMITS),
            },
            report,
            now_ms=completed_at_ms,
        )
        self.assertEqual(joined["identity"]["source_id"], host_name)
