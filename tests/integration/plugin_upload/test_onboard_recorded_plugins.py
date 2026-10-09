"""Onboard HTTP download preserves code after the remote catalog disappears."""

from __future__ import annotations

import json
import shutil
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from autonomy.decision_cycle.steps import decision_steps
from autonomy.runtime.recording import RunRecording
from autonomy.runtime.session import RunConfiguration
from cli.automa_cli.onboard_automation import monitor_onboard_runtime
from cli.automa_cli.runtime_view import RuntimeViewServer
from implementations.runtime.picar import create_host
from tests.cli.workbench_fixtures import image_source, post_action
from tests.integration.plugin_arming.test_flows import frame, upload, write_plugin
from tests.integration.plugin_upload.test_recorded_plugins import candidate, replay
from tests.support.cli_runner import run_automa


class OnboardRecordedPluginFlows(unittest.TestCase):
    def test_monitor_downloads_sources_before_replaying_a_moved_recording(self):
        with image_source(1) as root:
            host = create_host(steps=decision_steps())
            self.addCleanup(host.close)
            host.follow_activations({}, root / "runtime")
            host.register_status_provider("observation", lambda: {
                "frames_captured": host.recording.count if host.recording else 0,
                "skipped_count": 0,
            })
            view = RuntimeViewServer(vehicle_id="picar", automation_dir=root / "viewer",
                                     port=0, plugin_catalog=host.plugin_catalog).start()
            self.addCleanup(view.stop)
            source = root / "proposal.py"
            write_plugin(source, "proposal", "prototype", 7)
            _, uploaded = upload(view.url, source, "proposal", "prototype", arm=True)
            temporary_sources = Path(uploaded["plugin"]["metadata"]["source_path"]).parent
            recording = RunRecording(root / "remote", vehicle_id="picar")

            class OnboardTransport(BaseHTTPRequestHandler):
                """Emulate only the external Donkey HTTP envelope; the host is real."""

                def respond(self, payload):
                    body = json.dumps(payload).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def do_GET(self):
                    if self.path == "/autonomy/status":
                        self.respond({"ok": True, "autonomy": host.status()})
                    elif self.path == "/api/plugins":
                        self.respond(host.plugin_catalog())
                    else:
                        self.respond({"ok": True, "host_run_id": "onboard", "session": host.session_status()})

                def do_POST(self):
                    data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                    if data["command"] == "run":
                        host.start(RunConfiguration(**data["configuration"]), recording=recording)
                        for index in range(2):
                            host.run(frame(root, index))
                        # The downloader cannot read any original source path.
                        shutil.rmtree(temporary_sources)
                    elif data["command"] == "read_recording":
                        self.respond({"ok": True, "recording": recording.read(run_id=data["run_id"], after=data["after"])})
                        return
                    elif data["command"] == "stop":
                        host.stop()
                    self.respond({"ok": True, "host_run_id": "onboard", "session": host.session_status()})

                def log_message(self, *_args):
                    pass

            server = ThreadingHTTPServer(("127.0.0.1", 0), OnboardTransport)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                code, message = monitor_onboard_runtime(
                    vehicle_id="picar", base_url=base, automation_dir=root / "local",
                    perception={"preset": None, "plugins": []},
                    decision={"generation_id": host.applied_decision()["generation_id"], "steps": {}, "published": False},
                    step_activations={}, configuration=RunConfiguration(mode="observe_only", num_decisions=2),
                    timeout_s=3, record=True, verbose=False, output=None,
                )
                self.assertEqual(code, 0, message)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
            state = json.loads((root / "local" / "state.json").read_text())
            self.assertEqual(state["recorded_count"], 2)
            self.assertFalse(temporary_sources.exists())
            moved = root / "moved"
            shutil.move(Path(state["run_dir"]), moved)
            shutil.rmtree(root / "remote")
            host.close()
            result = run_automa("vehicles", "workbench", "replay", str(moved), "--cadence-ms", "0")
            self.assertIn("phase: completed", result.stdout)
            base, run_id = replay(self, moved)
            for position in range(2):
                displayed = post_action(base, {"action": "seek", "run_id": run_id, "position": position})["state"]
                self.assertEqual(candidate(displayed, "prototype")["metadata"]["revision"], 7)


if __name__ == "__main__":
    unittest.main()
