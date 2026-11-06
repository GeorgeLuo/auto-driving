"""A view's failures end only that view."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from autonomy.runtime.report import HostReport, ReportApplication
from cli.automa_cli.hosts.views import view_feed as viewer
from cli.automa_cli.plugins.stream import decision, session
from cli.automa_cli.plugins.stream.commands import stream_host_step
from cli.automa_cli.plugins.stream.models import StreamResult


class LiveViewCopyTests(unittest.TestCase):
    def test_unavailable_view_and_failed_fetch_return_a_probe_error(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(viewer, "runtime_view_dir", return_value=Path(tmp)),
            patch.object(viewer.RuntimeViewServer, "start", side_effect=OSError("port taken")),
            patch.object(viewer, "fetch_observation_sample", side_effect=ConnectionError("offline")),
        ):
            feed = viewer.ViewFeed("fixture", base_url="http://host.invalid", timeout_s=0.1)
            self.assertIsNone(feed.server)
            publication, error = feed.read_observation()
            self.assertIsNone(publication)
            self.assertIn("offline", error)
            feed.publish(publication)

    def test_decision_stream_pass_shares_one_observation_read_with_its_view(self):
        feed = Mock()
        publication = {"frame": {"frame_id": "one"}}
        feed.read_observation.return_value = publication, None
        def one_pass(**kwargs):
            kwargs["probe"]()
            return StreamResult(exit_code=0)
        normalized = {
            "result_age_ms": 0, "max_age_ms": 1000,
            "report": HostReport(
                host_name="fixture", run_id="run", generation_id="generation",
                frame_id="one", frame_index=0, timestamp_ms=1, published_at_ms=1,
                values={"activation": {"steps": {"plan": {"plugins": []}}}},
                cycle={"observation": None, "memory": None, "proposal": {}, "plan": {}, "action": {}},
                application=ReportApplication(False, "observe_only", "observe-only", 0, 0),
            ),
        }
        with (
            patch.object(decision, "require_valid_activations"),
            patch.object(decision, "runtime_base_url", return_value="http://host.invalid"),
            patch.object(session, "ViewFeed", return_value=feed),
            patch.object(decision, "poll_events", side_effect=one_pass),
            patch.object(decision, "fetch_decision_publication", return_value={}),
            patch.object(decision, "accept_decision_publication", return_value=normalized),
        ):
            result = stream_host_step("plan", host_name="fixture")
        self.assertEqual(result.exit_code, 0)
        feed.read_observation.assert_called_once_with()
        feed.publish.assert_called_once_with(publication)

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
