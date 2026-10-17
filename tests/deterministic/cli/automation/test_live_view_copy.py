"""Image availability cannot stop a run or freeze behind recording export."""
from __future__ import annotations

import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from autonomy.runtime.session import RunConfiguration
from cli.automa_cli import runtime_monitor
from cli.automa_cli.runtime_view import RuntimeViewServer
from tests.deterministic.cli.automation.test_runtime_monitor import (
    DECISION, SELECTIONS, _FakeDecisionView, _host, _publication, _session,
)
from tests.shared.implementations.runtime.wait import wait_until


class LiveViewCopyTests(unittest.TestCase):
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
            holder["server"] = RuntimeViewServer(**kwargs)
            return holder["server"]

        def export(*args, **kwargs):
            export_entered.set()
            if not export_release.wait(3):
                raise TimeoutError("test did not release recording export")
            args[1]["recorded_count"] = 2

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(runtime_monitor, "RuntimeClient", return_value=Client()),
            patch.object(runtime_monitor, "RuntimeViewServer", side_effect=server),
            patch.object(runtime_monitor, "DecisionViewAdapter", _FakeDecisionView),
            patch.object(runtime_monitor, "fetch_observation_sample", side_effect=sample),
            patch.object(runtime_monitor, "_sync_recording", side_effect=export),
        ):
            root = Path(tmp)
            worker = threading.Thread(target=lambda: results.append(runtime_monitor.monitor_runtime(
                vehicle_id="picar-test", base_url=Client.base_url, automation_dir=root,
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
