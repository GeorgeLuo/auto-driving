"""Image availability cannot stop a run or freeze behind recording export."""
from __future__ import annotations

import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

from autonomy.runtime.session import RunConfiguration
from cli.automa_cli.hosts.runs import monitor
from cli.automa_cli.hosts.views import view_feed as viewer
from cli.automa_cli.plugins.stream import decision, session
from cli.automa_cli.plugins.stream.commands import stream_host_step
from cli.automa_cli.plugins.stream.models import StreamResult
from cli.automa_cli.hosts.views.runtime_view import runtime_view_server
from tests.deterministic.cli.automation.test_runtime_monitor import (
    DECISION, SELECTIONS, _FakeDecisionView, _host, _publication, _session,
)
from tests.shared.implementations.runtime.wait import wait_until


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
            "decision": {"values": {"activation": {"steps": {"plan": {"plugins": []}}}},
                         "cycle": {"plan": {}}},
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

    def test_perception_recovers_and_advances_while_recording_export_is_blocked(self):
        export_entered, export_release = threading.Event(), threading.Event()
        current = {"index": None}
        holder = {}
        results = []
        output = io.BytesIO()
        Image.new("RGB", (4, 4)).save(output, "JPEG")

        class Client:
            base_url = "http://host.invalid"
            def host_status(self):
                return _host(3)
            def start(self, configuration, **kwargs):
                return {"ok": True, "host_run_id": "host-1", "session": {"recording": {"run_id": "recording-1"}}}
            def status(self):
                return {"ok": True, "host_run_id": "host-1",
                        "session": _session("completed" if export_release.is_set() else "running", 2)}
            def stop(self):
                return {"ok": True, "session": _session("stopped", 2)}

        def sample(*args, **kwargs):
            if current["index"] is None:
                raise ValueError("temporary image error")
            return _publication(current["index"]), output.getvalue()

        def server(**kwargs):
            holder["server"] = runtime_view_server(**kwargs)
            return holder["server"]

        def export(*args, **kwargs):
            export_entered.set()
            if not export_release.wait(3):
                raise TimeoutError("test did not release recording export")
            args[1]["recorded_count"] = 2

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(monitor, "RuntimeClient", return_value=Client()),
            patch.object(monitor, "runtime_view_server", side_effect=server),
            patch.object(monitor, "DecisionViewAdapter", _FakeDecisionView),
            patch.object(monitor, "fetch_observation_sample", side_effect=sample),
            patch.object(monitor, "_sync_recording", side_effect=export),
        ):
            root = Path(tmp)
            worker = threading.Thread(target=lambda: results.append(monitor.monitor_runtime(
                host_name="picar-test", base_url=Client.base_url, automation_dir=root,
                selections=SELECTIONS, decision=dict(DECISION),
                configuration=RunConfiguration(mode="observe_only", num_decisions=2),
                timeout_s=0.2, record=True, verbose=False, output=None,
            )), daemon=True)
            worker.start()
            try:
                self.assertTrue(export_entered.wait(2))
                self.assertIsNone(holder["server"].perception.latest_json())
                current["index"] = 1

                def latest():
                    raw = holder["server"].perception.latest_json()
                    return json.loads(raw) if raw else None

                published = wait_until(latest, message="perception waited for recording export")
                self.assertEqual(published["frame"]["frame_id"], "donkey_frame_000001")
                self.assertTrue(worker.is_alive())
                self.assertFalse(export_release.is_set())
            finally:
                export_release.set()
                worker.join(3)
            self.assertFalse(worker.is_alive())
            self.assertEqual(results[0][0], 0, results)
