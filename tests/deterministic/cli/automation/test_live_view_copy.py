"""A view's failures end only that view."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autonomy.runtime.frame_loop import FramePublication
from autonomy.runtime.report import HostReport, ReportCycle, ReportApplication, ReportValues
from cli.automa_cli.hosts.views import view_feed as viewer
from cli.automa_cli.plugins.stream import decision, session


class LiveViewCopyTests(unittest.TestCase):
    def test_unavailable_view_and_failed_fetch_return_a_probe_error(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(viewer, "runtime_view_dir", return_value=Path(tmp)),
            patch.object(viewer.RuntimeViewServer, "start", side_effect=OSError("port taken")),
            patch.object(viewer, "fetch_frame_sample", side_effect=ConnectionError("offline")),
        ):
            feed = viewer.ViewFeed("fixture", base_url="http://host.invalid", timeout_s=0.1)
            self.assertIsNone(feed.server)
            publication, error = feed.read_observation()
            self.assertIsNone(publication)
            self.assertIn("offline", error)
            feed.publish(publication)

    def test_decision_stream_pass_shares_one_observation_read_with_its_view(self):
        publication = FramePublication(
            health="healthy", read_at_ms=1, result_age_ms=0, stale_after_ms=1000,
            report=HostReport(
                host_name="fixture", run_id="run", generation_id="generation",
                frame_id="one", frame_index=0, timestamp_ms=1, published_at_ms=1,
                values=ReportValues.from_dict({
                    "source_id": "fixture:local", "stale_after_ms": 1000,
                    "activation": {"steps": {"plan": {"plugins": []}}},
        "delivered_at_ms": None, "delivery_error": None, "receipt": None, "timing": None,
                }),
                cycle=ReportCycle(),
                application=ReportApplication(False, "observe_only", "observe-only", 0, 0),
            ),
        )
        events = []
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(viewer, "runtime_view_dir", return_value=Path(tmp)),
            patch.object(decision, "require_valid_activations"),
            patch.object(decision, "runtime_base_url", return_value="http://host.invalid"),
            patch.object(viewer, "fetch_frame_sample", return_value=(publication, None)) as read,
            patch("cli.automa_cli.hosts.views.decision_live.fetch_host_telemetry_latest",
                  side_effect=ConnectionError("no telemetry")),
            patch.object(decision, "fetch_frame_publication", side_effect=AssertionError("second frame fetch")),
        ):
            feed = viewer.ViewFeed("fixture", base_url="http://host.invalid", timeout_s=0.1)
            self.addCleanup(feed.stop)
            view = feed.server.perception
            with patch.object(session, "ViewFeed", return_value=feed):
                result = decision.stream_decision(
                    "plan", host_name="fixture", refresh_s=0.5, once=True, timeout_s=0.1,
                    serve_view=True, emit=events.append,
                )
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(read.call_count, 1)
        self.assertEqual(len(events), 1)
        shown = json.loads(view.latest_json())
        self.assertEqual(events[0].live["frame_id"], shown["report"]["frame_id"])
        self.assertEqual(events[0].live["record"], shown["report"]["cycle"]["plan"])
        self.assertIsNone(feed.decision_error)
        self.assertIsNone(feed.server)

    def test_a_partial_start_releases_its_socket_and_stop_is_idempotent(self):
        bound = []

        def thread_fails(httpd, *, name):
            bound.append(httpd)
            raise RuntimeError("no thread")

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(viewer, "runtime_view_dir", return_value=Path(tmp)),
            patch("cli.automa_cli.frontend.server.start_server_thread", side_effect=thread_fails),
        ):
            feed = viewer.ViewFeed("fixture", base_url="http://host.invalid", timeout_s=0.1)

        self.assertEqual(feed.error, "RuntimeError: no thread")
        self.assertIsNone(feed.server)
        self.assertIsNone(feed.url)
        # The bound socket closed without shutdown(), which would wait forever.
        self.assertEqual(bound[0].socket.fileno(), -1)
        feed.stop()
        feed.publish({"frame": {"frame_id": "one"}})
