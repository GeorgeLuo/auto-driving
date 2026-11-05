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
from autonomy.runtime.report import HostReport, ReportApplication
from cli.automa_cli.frontend.perception_view import PerceptionView
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
                timeout_s=0.2, record=True, verbose=False,
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
                served, content_type = holder["server"].perception.frame()
                self.assertEqual(content_type, "image/jpeg")
                self.assertEqual(served, output.getvalue())
                # The run's latest files follow the view's publication.
                front_camera = root / "latest" / "frames" / "latest_front_camera.jpg"
                wait_until(
                    lambda: front_camera.exists() and front_camera.read_bytes() == output.getvalue(),
                    message="the monitor did not export the published frame",
                )
                self.assertTrue(worker.is_alive())
                self.assertFalse(export_release.is_set())
            finally:
                export_release.set()
                worker.join(3)
            self.assertFalse(worker.is_alive())
            self.assertEqual(results[0].exit_code, 0, results)

    def test_latest_exports_retry_after_failure_without_rewriting_unchanged_captures(self):
        for failed_export in ("latest_perception.json", "latest_perception.txt"):
            with self.subTest(export=failed_export), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                jpeg = io.BytesIO()
                Image.new("RGB", (4, 5)).save(jpeg, "JPEG")
                first, second = _publication(1), _publication(2)
                # The monitor still accepts a stopped host's supplied last image.
                first["health"] = second["health"] = "stale"
                identity = {"run_id": "run-1", "control_source": "runtime_host"}
                server = Mock(perception=PerceptionView(host_name="fixture"))
                copied = {"publication": None, "ready": threading.Event()}
                stop = threading.Event()
                polls = 0
                write_text = Path.write_text
                write_json = monitor.write_json_atomically

                def export_json(path, record):
                    if polls == 2 and failed_export == path.name:
                        raise PermissionError("json export blocked")
                    write_json(path, record)

                def export_text(path, text, **kwargs):
                    if polls == 2 and failed_export == path.name:
                        raise PermissionError("text export blocked")
                    return write_text(path, text, **kwargs)

                def check_poll(_timeout):
                    nonlocal polls
                    self.assertTrue(copied["ready"].is_set())
                    if polls in (2, 4, 6):
                        self.assertIsNone(server.perception.latest_json())
                        if polls == 2:
                            self.assertIn("export blocked", server.perception.unavailable_reason)
                    else:
                        publication = first if polls < 2 else second
                        record = {**publication, **identity}
                        self.assertEqual(json.loads((root / "latest_perception.json").read_text()), record)
                        self.assertEqual((root / "latest_perception.txt").read_text(), "signal id=lane\n")
                        self.assertEqual((root / "frame.jpg").read_bytes(), jpeg.getvalue())
                        payload = json.loads(server.perception.latest_json())
                        self.assertEqual(payload["frame"]["frame_id"], publication["frame"]["frame_id"])
                        self.assertEqual((payload["frame"]["width_px"], payload["frame"]["height_px"]), (4, 5))
                        self.assertEqual(payload["overlay"]["status"], "current")
                        self.assertEqual(copied["publication"], publication)
                    polls += 1
                    if polls == 8:
                        stop.set()

                samples = [
                    (first, jpeg.getvalue()), (first, jpeg.getvalue()),
                    (second, jpeg.getvalue()), (second, jpeg.getvalue()),
                    ConnectionError("offline"), (second, jpeg.getvalue()),
                    ({"health": "warming"}, None), (second, jpeg.getvalue()),
                ]
                with (
                    patch.object(monitor, "fetch_observation_sample", side_effect=samples) as read,
                    patch.object(stop, "wait", side_effect=check_poll),
                    patch.object(monitor, "write_json_atomically", side_effect=export_json) as json_export,
                    patch.object(Path, "write_text", autospec=True, side_effect=export_text) as text_export,
                    patch.object(server.perception, "publish", wraps=server.perception.publish) as publish,
                ):
                    monitor._copy_latest_perception(
                        "http://host.invalid", server, stop, copied,
                        front_camera_path=root / "frame.jpg", json_path=root / "latest_perception.json",
                        text_path=root / "latest_perception.txt", identity=identity, timeout_s=0.1,
                    )
                self.assertEqual(read.call_count, 8)
                self.assertEqual(publish.call_count, 5)
                self.assertEqual(json_export.call_count, 5)
                self.assertEqual(text_export.call_count, 4 if failed_export.endswith("json") else 5)
                self.assertEqual(publish.call_args.kwargs["frame_record"], {**second, **identity})
