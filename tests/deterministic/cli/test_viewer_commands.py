"""The viewer answers vehicles, simulators, hosts and plugins commands as its read and write routes."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from PIL import Image

from cli.automa_cli.frontend.server import RuntimeViewServer
from cli.automa_cli.hosts.views import frames
from cli.automa_cli.providers.urls import chase_operator_url
from cli.automa_cli.viewer_commands import VIEWER_READS, VIEWER_WRITES
from tests.shared.cli.workbench_fixtures import ImageReplayRunner
from tests.support.fake_metrics_ui import fake_metrics_ui
from tests.support.runtime_fixtures import runtime_root_env


class ViewerCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(runtime_root_env(root))
        self.server = RuntimeViewServer(
            host_name="viewer", automation_dir=None, port=0, connected=False,
            reads=VIEWER_READS, writes=VIEWER_WRITES, replay_runner_factory=ImageReplayRunner,
        ).start()
        self.addCleanup(self.server.stop)

    def get(self, route: str) -> dict:
        with urlopen(f"{self.server.url}{route.lstrip('/')}", timeout=5.0) as response:
            return json.loads(response.read())

    def refused(self, route: str) -> tuple[int, dict]:
        with self.assertRaises(HTTPError) as caught:
            urlopen(f"{self.server.url}{route.lstrip('/')}", timeout=5.0)
        return caught.exception.code, json.loads(caught.exception.read())

    def post(self, route: str, body: object, **headers: str) -> tuple[int, dict]:
        request = Request(
            f"{self.server.url}{route.lstrip('/')}", data=json.dumps(body).encode(), method="POST",
            headers={"Content-Type": "application/json", **headers},
        )
        try:
            with urlopen(request, timeout=5.0) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            return error.code, json.loads(error.read())

    def test_a_discovered_vehicle_is_listed_until_it_is_removed(self) -> None:
        self.assertEqual(self.get("/api/vehicles")["vehicles"], [])
        with fake_metrics_ui() as simulator:
            control_url, url = simulator.url, chase_operator_url(simulator.url)

            code, discovered = self.post("/api/vehicles/discover", {"provider": "chase-sim", "url": url, "force": True})
            listed = self.get("/api/vehicles")["vehicles"]
            removed = self.post("/api/vehicles/remove", {"url": url})

        self.assertEqual((code, discovered["ok"], discovered["exit_code"], discovered["kind"]), (200, True, 0, "discovered"))
        self.assertIn(url, discovered["text"])
        self.assertEqual((discovered["provider"], discovered["url"]), ("chase-sim", control_url))
        self.assertEqual([vehicle["url"] for vehicle in listed], [url])
        self.assertEqual((removed[0], removed[1]["kind"]), (200, "removed"))
        self.assertTrue(removed[1]["text"].startswith("Removed "), removed[1]["text"])
        self.assertEqual(self.get("/api/vehicles")["vehicles"], [])

    def test_a_command_that_fails_answers_400_with_what_it_prints(self) -> None:
        missing = self.post("/api/simulators/delete", {"url": "http://127.0.0.1:5099/"})
        with mock.patch.dict(os.environ, {"AUTOMA_METRICS_UI_DIR": ""}):
            uncreated = self.post("/api/simulators/create", {})

        self.assertEqual((missing[0], missing[1]["ok"], missing[1]["exit_code"], missing[1]["kind"]), (400, False, 2, "missing"))
        self.assertEqual((uncreated[0], uncreated[1]["kind"]), (400, "create_error"))
        self.assertTrue(uncreated[1]["text"].startswith("Cannot create a simulator: AUTOMA_METRICS_UI_DIR"), uncreated[1])
        self.assertEqual(self.get("/api/simulators"), {"schema": "automa_simulators_status", "simulators": []})

    def test_a_malformed_request_is_refused_before_the_command_runs(self) -> None:
        self.assertEqual(self.post("/api/vehicles/discover", {"provider": "boat", "url": "x"}),
                         (400, {"ok": False, "error": "provider must be one of chase-sim, picar"}))
        self.assertEqual(self.post("/api/vehicles/remove", {}),
                         (400, {"ok": False, "error": "url must be a non-empty string"}))
        self.assertEqual(self.post("/api/vehicles/remove", ["url"])[1]["error"], "request body must be an object")

    def test_hosts_status_reads_as_its_json_prints(self) -> None:
        self.assertEqual(self.get("/api/hosts/status"), {"schema": "automa_hosts_status", "host_name": None, "hosts": []})

    def test_a_restart_takes_run_flags_and_movement_without_a_count_is_refused(self) -> None:
        code, restart = self.post("/api/hosts/restart", {"name": "chaser", "observe_only": False})

        self.assertEqual((code, restart["ok"], restart["kind"], restart["stop"]), (400, False, "error", None))
        self.assertEqual(restart["text"], "A run that applies control needs --num-decisions N (N > 0); "
                                          "use --unbounded to run until stopped.")
        self.assertEqual(self.post("/api/hosts/run", {"name": "chaser", "num_decisions": "10"}),
                         (400, {"ok": False, "error": "num_decisions must be a whole number or null"}))
        self.assertEqual(self.post("/api/hosts/stop", {}),
                         (400, {"ok": False, "error": "name must be a non-empty string"}))

    def test_plugins_list_reads_as_its_json_prints(self) -> None:
        packaged = self.get("/api/plugins/list")
        code, missing = self.refused("/api/plugins/list?host_name=chaser")

        self.assertEqual((packaged["schema"], packaged["ok"], packaged["host_name"]), ("automa_plugin_list", True, None))
        self.assertEqual(sorted(packaged["step_presets"]),
                         ["action", "memory", "observation", "perception", "plan", "proposal"])
        self.assertTrue(packaged["step_presets"]["perception"]["lightweight_observer"]["default"])
        self.assertEqual((code, missing["schema"], missing["ok"], missing["host_name"]), (400, "automa_plugin_list", False, "chaser"))
        self.assertIn("chaser", missing["error"])

    def test_a_stream_probe_answers_what_plugins_stream_once_prints(self) -> None:
        unavailable = self.get("/api/plugins/stream?host_name=chaser&step=plan")

        self.assertEqual((unavailable["schema"], unavailable["status"], unavailable["host_name"]),
                         ("host_plan_stream", "unavailable", "chaser"))
        self.assertEqual(unavailable["text"], unavailable["error"])
        self.assertEqual(self.refused("/api/plugins/stream?host_name=chaser&step=observation"),
                         (400, {"ok": False, "error": "step must be one of perception, memory, proposal, plan, action"}))
        self.assertEqual(self.refused("/api/plugins/stream?step=plan"),
                         (400, {"ok": False, "error": "host_name must be a non-empty string"}))

    def test_a_hosts_latest_frame_names_its_image_at_the_frame_route(self) -> None:
        buffer = io.BytesIO()
        Image.new("RGB", (8, 6), (20, 40, 60)).save(buffer, format="JPEG")
        report = {"frame_id": "frame 1", "frame_index": 1, "run_id": "run", "generation_id": "decision",
                  "status": "ok", "timestamp_ms": 5, "cycle": {}}
        publication = {"schema": "host_frame_publication", "health": "healthy", "ok": True, "report": report}
        with (mock.patch.object(frames, "runtime_base_url", return_value="http://host.invalid"),
              mock.patch.object(frames, "fetch_frame_sample", return_value=(publication, buffer.getvalue()))):
            latest = self.get("/api/hosts/latest?host_name=framed")
        with urlopen(f"{self.server.url}{latest['image']['url'].lstrip('/')}", timeout=5.0) as response:
            image = response.status, response.headers["Content-Type"], response.read()

        self.assertEqual((latest["schema"], latest["host_name"], latest["report"]["frame_id"]),
                         ("host_frame_publication", "framed", "frame 1"))
        self.assertEqual(latest["image"]["url"],
                         "/api/hosts/frame?host_name=framed&v=%5B%22run%22%2C%22decision%22%2C%22frame+1%22%5D")
        self.assertEqual(image, (200, "image/jpeg", buffer.getvalue()))
        self.assertEqual(self.refused("/api/hosts/frame?host_name=framed&v=gone"),
                         (400, {"ok": False, "error": "That frame's image is no longer available."}))
        code, missing = self.refused("/api/hosts/latest?host_name=chaser")
        self.assertEqual((code, missing["ok"]), (400, False))
        self.assertIn("chaser", missing["error"])

    def test_a_malformed_plugins_request_is_refused_before_the_command_runs(self) -> None:
        steps = "perception, observation, memory, proposal, plan, action"
        for route, body, error in [
            ("/api/plugins/arm", {"host_name": "chaser", "preset": "lightweight_observer"},
             f"preset must name <step>/<id> with a step from {steps}"),
            ("/api/plugins/arm", {"host_name": "chaser", "name": "perception/frame"},
             "name must be a non-empty list of <step>/<plugin>"),
            ("/api/plugins/disarm", {"host_name": "chaser"}, "disarm needs name, step or all"),
            ("/api/plugins/upload", {"host_name": "chaser", "name": "perception/prototype", "entrypoint": "p:P",
                                     "filename": "p.py", "source_base64": "not base64!"},
             "source_base64 is not base64: Only base64 data is allowed"),
            ("/api/plugins/save", {"host_name": "chaser", "step": "steering"}, f"step must be one of {steps}"),
        ]:
            with self.subTest(route=route, body=body):
                self.assertEqual(self.post(route, body), (400, {"ok": False, "error": error}))

    def test_writes_come_only_from_the_viewer_itself(self) -> None:
        self.assertEqual(self.post("/api/vehicles/remove", {"url": "x"}, Origin="http://example.com")[0], 403)
        self.assertEqual(self.post("/api/vehicles/remove", {"url": "x"}, **{"Content-Type": "text/plain"}),
                         (415, {"ok": False, "error": "writes require application/json"}))
        with self.assertRaises(HTTPError) as caught:
            urlopen(Request(f"{self.server.url}api/vehicles", data=b"{}", method="POST",
                            headers={"Content-Type": "application/json"}), timeout=5.0)
        self.assertEqual(caught.exception.code, 405)


if __name__ == "__main__":
    unittest.main()
