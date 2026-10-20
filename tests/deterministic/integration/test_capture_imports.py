"""Capture fixtures retain host types while a staged runner swaps modules."""

import threading
import unittest
from pathlib import Path

from autonomy.runtime.plugin_loader import BUNDLE_PREFIXES, StagedBundleImport
from implementations.vehicle.chase_sim import ChaseCameraCapture
from tests.shared.cli.automation_pipeline.pipeline_fixtures import _FakeCar

ROOT = Path(__file__).resolve().parents[3]


class CaptureImportTests(unittest.TestCase):
    def test_capture_keeps_host_type_while_staged_modules_are_active(self) -> None:
        stream = _FakeCar().camera_stream()
        self.addCleanup(stream.close)
        captures = []
        errors = []
        stream.set_consumer(captures.append)

        def emit():
            try:
                stream.emit()
            except Exception as error:
                errors.append(error)

        capture_thread = threading.Thread(target=emit, name="test-capture", daemon=True)
        with StagedBundleImport(ROOT, BUNDLE_PREFIXES).activate():
            capture_thread.start()
            capture_thread.join(timeout=5)

        self.assertFalse(capture_thread.is_alive(), "capture blocked on staged imports")
        self.assertEqual(errors, [])
        self.assertEqual(len(captures), 1)
        self.assertIs(type(captures[0]), ChaseCameraCapture)
        self.assertEqual(captures[0].frame_index, 100)
