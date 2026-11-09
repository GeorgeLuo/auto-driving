"""The viewer answers vehicles, simulators, hosts and plugins commands as its read and write routes."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from itertools import combinations
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from PIL import Image

from cli.automa_cli import viewer_commands
from cli.automa_cli.frontend.server import RuntimeViewServer
from cli.automa_cli.hosts.deployment.models import CreateResult, UpdateResult
from cli.automa_cli.hosts.errors import RuntimeHostError
from cli.automa_cli.hosts.views import frames
from cli.automa_cli.providers.urls import chase_operator_url
from cli.automa_cli.viewer_commands import VIEWER_READS, VIEWER_WRITES
from tests.shared.cli.workbench_fixtures import ImageReplayRunner
from tests.support.fake_metrics_ui import fake_metrics_ui
from tests.support.runtime_fixtures import runtime_root_env


class HostFrameCacheTests(unittest.TestCase):
    def test_unknown_hosts_are_not_cached(self) -> None:
        cache = frames.HostFrames(frame_route="/api/hosts/frame")
        with (mock.patch.object(frames, "runtime_base_url", side_effect=RuntimeHostError("unknown host")),
              mock.patch.object(frames, "fetch_frame_sample") as fetch):
            for index in range(100):
                self.assertFalse(cache.latest(f"missing-{index}")["ok"])
            fetch.assert_not_called()
        self.assertEqual(len(cache._hosts), 0)

    def test_host_images_are_bounded_and_the_least_recently_used_host_is_evicted(self) -> None:
        cache = frames.HostFrames(frame_route="/api/hosts/frame")
        buffer = io.BytesIO()
        Image.new("RGB", (2, 2)).save(buffer, format="JPEG")
        report = {"frame_id": "one", "frame_index": 1, "run_id": "run", "generation_id": "decision",
                  "status": "ok", "timestamp_ms": 5, "cycle": {}}
        publication = {"schema": "host_frame_publication", "health": "healthy", "ok": True, "report": report}
        with (mock.patch.object(frames, "MAX_HOSTS", 2),
              mock.patch.object(frames, "runtime_base_url", return_value="http://host.invalid") as resolve,
              mock.patch.object(frames, "fetch_frame_sample", return_value=(publication, buffer.getvalue()))):
            first = cache.latest("first")
            second = cache.latest("second")
            first_id = parse_qs(urlparse(first["image"]["url"]).query)["v"][0]
            second_id = parse_qs(urlparse(second["image"]["url"]).query)["v"][0]
            self.assertEqual(cache.image("first", first_id), (buffer.getvalue(), "image/jpeg"))
            cache.latest("third")
            self.assertFalse(cache.image("second", second_id)["ok"])
            self.assertEqual(cache.image("first", first_id), (buffer.getvalue(), "image/jpeg"))
            self.assertEqual(len(cache._hosts), 2)
            resolve.side_effect = RuntimeHostError("host deleted")
            self.assertFalse(cache.latest("first")["ok"])
            self.assertFalse(cache.image("first", first_id)["ok"])


class ViewerCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.root = root
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

    def test_host_deployment_forwards_options_and_rejects_unrecognized_fields(self) -> None:
        create = {"name": "car", "provider": "picar", "url": "http://car", "dry_run": True,
                  "ssh_target": "pi@car", "pi_home": "/home/pi", "drive_args": "--manual", "verbose": True}
        update = {"name": "car", "dry_run": True, "restart": True, "drive_args": "--manual", "verbose": True}
        with (mock.patch("cli.automa_cli.viewer_commands.create_host", return_value=CreateResult(2, "error", "test")) as create_host,
              mock.patch("cli.automa_cli.viewer_commands.update_host", return_value=UpdateResult(2, "error", "test")) as update_host):
            self.post("/api/hosts/create", create)
            self.post("/api/hosts/update", update)
            self.assertEqual(create_host.call_args.kwargs, {
                "host_name": "car", "provider": "picar", "url": "http://car", "dry_run": True,
                "ssh_target": "pi@car", "pi_home": "/home/pi", "drive_args": "--manual", "verbose": True, "emit": None,
            })
            self.assertEqual(update_host.call_args.kwargs, {
                "host_name": "car", "dry_run": True, "restart": True, "drive_args": "--manual", "verbose": True, "emit": None,
            })
            create_host.reset_mock()
            update_host.reset_mock()
            for route, body in [("create", {**create, "restart": True}),
                                ("update", {**update, "ssh_target": "other"}),
                                ("create", {**create, "dry_run": "true"}),
                                ("update", {**update, "restart": 1})]:
                with self.subTest(route=route, body=body):
                    self.assertEqual(self.post(f"/api/hosts/{route}", body)[0], 400)
            create_host.assert_not_called()
            update_host.assert_not_called()

    def test_create_dry_run_plans_without_creating_a_host(self) -> None:
        code, result = self.post("/api/hosts/create", {
            "name": "car", "provider": "picar", "url": "http://example.local:8887", "dry_run": True,
            "ssh_target": "pi@example.local", "pi_home": "/home/pi",
        })
        self.assertEqual((code, result["ok"], result["kind"]), (200, True, "plan"))
        self.assertEqual(result["configuration"]["connection"]["ssh_target"], "pi@example.local")
        self.assertFalse((self.root / "car").exists())

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
            ("/api/plugins/arm", {"host_name": "chaser", "collection": ""}, "collection must be a non-empty string"),
            ("/api/plugins/disarm", {"host_name": "chaser"}, "disarm needs name, collection, step or all"),
            ("/api/plugins/disarm", {"host_name": "chaser", "collection": 413}, "collection must be a non-empty string"),
            ("/api/plugins/upload", {"host_name": "chaser", "manifest": ""}, "manifest must be a non-empty string"),
            ("/api/plugins/upload", {"host_name": "chaser", "name": "perception/prototype", "entrypoint": "p:P",
                                     "filename": "p.py", "source_base64": "not base64!"},
             "source_base64 is not base64: Only base64 data is allowed"),
            ("/api/plugins/save", {"host_name": "chaser", "step": "steering"}, f"step must be one of {steps}"),
        ]:
            with self.subTest(route=route, body=body):
                self.assertEqual(self.post(route, body), (400, {"ok": False, "error": error}))

    def test_conflicting_plugin_selectors_are_refused_before_any_operation(self) -> None:
        arm = {"name": ["perception/p"], "preset": "perception/p", "collection": "pack"}
        disarm = {"name": ["perception/p"], "collection": "pack", "step": "perception", "all": True}
        with (mock.patch("cli.automa_cli.viewer_commands.arm_collection") as arm_collection,
              mock.patch("cli.automa_cli.viewer_commands.arm_preset") as arm_preset,
              mock.patch("cli.automa_cli.viewer_commands.arm_plugins") as arm_plugins,
              mock.patch("cli.automa_cli.viewer_commands.disarm_collection") as disarm_collection,
              mock.patch("cli.automa_cli.viewer_commands.disarm_plugins") as disarm_plugins,
              mock.patch("cli.automa_cli.viewer_commands.upload_collection") as upload_collection,
              mock.patch("cli.automa_cli.viewer_commands.upload_plugin") as upload_plugin):
            for route, selectors in (("arm", arm), ("disarm", disarm)):
                for first, second in combinations(selectors, 2):
                    body = {"host_name": "car", first: selectors[first], second: selectors[second]}
                    with self.subTest(route=route, body=body):
                        self.assertEqual(self.post(f"/api/plugins/{route}", body)[0], 400)
            for field in ("name", "entrypoint", "filename", "source_base64", "config"):
                body = {"host_name": "car", "manifest": "pack.json", field: "unexpected"}
                with self.subTest(field=field):
                    self.assertEqual(self.post("/api/plugins/upload", body)[0], 400)
            self.assertEqual(self.post("/api/plugins/disarm", {"host_name": "car", "collection": "pack", "all": "yes"})[0], 400)
            for operation in (arm_collection, arm_preset, arm_plugins, disarm_collection, disarm_plugins,
                              upload_collection, upload_plugin):
                operation.assert_not_called()

    def test_false_all_does_not_conflict_with_another_disarm_selector(self) -> None:
        with mock.patch.object(viewer_commands, "disarm_collection", wraps=viewer_commands.disarm_collection) as disarm:
            self.post("/api/plugins/disarm", {"host_name": "missing", "collection": "pack", "all": False})
        disarm.assert_called_once_with(url=None, host_name="missing", name="pack")

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
