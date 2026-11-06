from __future__ import annotations

import unittest
from copy import deepcopy
from urllib.error import HTTPError
from urllib.request import urlopen
from unittest.mock import patch

from tests.deterministic.cli.decision.live_runtime_decision_view_fixtures import LiveRuntimeDecisionViewFixture
from tests.shared.cli.decision.decision_surfaces_fixtures import STALE_AFTER_MS


class LiveRuntimeDecisionViewTests(LiveRuntimeDecisionViewFixture, unittest.TestCase):
    def test_stale_and_error_frames_with_null_steps_remain_readable(self):
        for error in (None, "proposal failed"):
            with self.subTest(error=error):
                report = self._accepted_frame()
                report["cycle"].update(proposal=None, plan=None, action=None)
                report.update(status="error" if error else "ok", error=error)
                self._publish_exact_transaction(stream_frame=report)
                now = report["published_at_ms"] + STALE_AFTER_MS + 1
                with patch("cli.automa_cli.frontend.perception_view._timestamp_ms", return_value=now):
                    payload = self._latest_payload()
                self.assertGreater(payload["result_age_ms"], STALE_AFTER_MS)
                self.assertEqual(payload["report"], report)
                self.assertIsNotNone(payload["image"]["url"])

    def test_invalidation_does_not_serve_a_cached_success(self):
        self._publish_exact_transaction()
        self.server.perception.invalidate_latest("offline")
        with self.assertRaises(HTTPError) as caught:
            urlopen(f"{self.server.url}api/latest", timeout=1)
        self.assertEqual(caught.exception.code, 503)
        self.assertIn("offline", caught.exception.read().decode())

    def test_published_report_is_detached_from_http_readers(self):
        report, _image = self._publish_exact_transaction()
        payload = self._latest_payload()
        payload["report"]["cycle"]["proposal"] = None
        self.assertEqual(self._latest_payload()["report"], report)

    def test_missing_image_keeps_the_next_report_and_no_previous_image_url(self):
        self._publish_exact_transaction()
        report = deepcopy(self._accepted_frame())
        report["frame_id"] = "missing-image"
        self.server.perception.publish(frame_record={"report": report}, frame_bytes=None)
        payload = self._latest_payload()
        self.assertEqual(payload["report"], report)
        self.assertIsNone(payload["image"]["url"])

    def test_repeated_frame_id_in_another_run_keeps_each_images_identity(self):
        report, first = self._publish_exact_transaction()
        first_url = self._latest_payload()["image"]["url"]
        replacement = self._accepted_frame(run_id="another-run")
        _report, second = self._publish_exact_transaction(stream_frame=replacement, image_color=(200, 40, 20))
        second_url = self._latest_payload()["image"]["url"]
        self.assertNotEqual(first_url, second_url)
        for path, expected in ((first_url, first), (second_url, second)):
            with urlopen(self.server.url.rstrip("/") + path, timeout=1) as response:
                self.assertEqual(response.read(), expected)
