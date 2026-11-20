from __future__ import annotations

import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.parse import urlencode, urljoin
from urllib.request import urlopen

from cli.automa_cli import app
from cli.automa_cli.frontend.server import RuntimeViewServer
from cli.automa_cli.frontend.workbench_view import WORKBENCH_CONTENT_SECURITY_POLICY
from cli.automa_cli.hosts.views import runtime_view
from cli.automa_cli.hosts.views.runtime_view import runtime_view_server
from implementations.vehicle.chase_sim.defaults import CHASE_UI_WS_URL_ENV
from implementations.vehicle.chase_sim.metrics_ws import MetricsUiWsClient
from implementations.vehicle.chase_sim.presence import MetricsUiPresence
from tests.shared.cli.automation_pipeline.pipeline_fixtures import chase_runtime
from tests.shared.cli.workbench_fixtures import FixtureMapper, ImageReplayRunner, image_source, post_action
from tests.shared.implementations.runtime.wait import wait_until
from tests.support.fake_hosts import closed_url
from tests.support.fake_metrics_ui import fake_metrics_ui


class RuntimeViewStartupTests(unittest.TestCase):
    def test_failed_serving_thread_start_closes_the_bound_socket_without_shutdown(self):
        server = runtime_view_server(host_name="viewer", port=0)
        with mock.patch("cli.automa_cli.frontend.server.start_server_thread",
                        side_effect=RuntimeError("thread could not start")):
            with self.assertRaisesRegex(RuntimeError, "thread could not start"):
                server.start()
        httpd = server._httpd
        self.addCleanup(httpd.server_close)
        with mock.patch.object(httpd, "shutdown", side_effect=AssertionError("no serving thread")) as shutdown:
            server.stop()
        shutdown.assert_not_called()
        self.assertEqual(httpd.socket.fileno(), -1)
        self.assertIsNone(server.url)

    def test_a_non_loopback_binding_is_refused_before_start(self) -> None:
        for host in ("0.0.0.0", "192.0.2.1"):
            with self.subTest(host=host), self.assertRaisesRegex(ValueError, "loopback"):
                RuntimeViewServer(host_name="viewer", host=host, port=0, replay_runner_factory=ImageReplayRunner)


class ViewerWithoutHostTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = runtime_view_server(
            host_name="viewer",
            port=0,
            ).start()
        self.addCleanup(self.server.stop)

    def test_the_root_opens_the_v2_page(self) -> None:
        for route in ("", "index.html"):
            with self.subTest(route=route):
                with urlopen(f"{self.server.url}{route}", timeout=1.0) as response:
                    self.assertEqual(response.url, f"{self.server.url}v2/")
                    self.assertIn('id="workbenchLink"', response.read().decode("utf-8"))

    def test_the_v1_pages_and_their_routes_are_gone(self) -> None:
        for route in ("perception", "memory", "decision", "plugins", "api/latest", "frame", "api/plugins"):
            with self.subTest(route=route):
                with self.assertRaises(HTTPError) as caught:
                    urlopen(f"{self.server.url}{route}", timeout=1.0)
                self.assertEqual(caught.exception.code, 404)

    def test_health_names_the_viewer(self) -> None:
        health = json.loads(_read_text(f"{self.server.url}api/health"))
        self.assertEqual((health["host_name"], health["url"], health["pid"]), ("viewer", self.server.url, os.getpid()))

    def test_shared_page_files_are_served(self) -> None:
        for name, content_type in (("float.js", "javascript"), ("float.css", "css")):
            with self.subTest(name=name):
                with urlopen(f"{self.server.url}static/shared/{name}", timeout=1.0) as response:
                    self.assertEqual(response.status, 200)
                    self.assertIn(content_type, response.headers["Content-Type"])
        with self.assertRaises(HTTPError) as caught:
            urlopen(f"{self.server.url}static/shared/missing.js", timeout=1.0)
        self.assertEqual(caught.exception.code, 404)

    def test_every_file_the_v2_page_loads_resolves_from_its_url(self) -> None:
        page_url = f"{self.server.url}v2/"
        with urlopen(page_url, timeout=1.0) as response:
            self.assertEqual(response.headers["Content-Security-Policy"], WORKBENCH_CONTENT_SECURITY_POLICY)
            page = response.read().decode("utf-8")
        loads = re.findall(r'<(?:script src|link rel="stylesheet" href)="([^"]+)"', page)
        self.assertIn("../shared/float.js", loads)
        workbench = re.search(r'<a class="nav" href="([^"]+)" id="workbenchLink"', page).group(1)
        with urlopen(urljoin(page_url, workbench), timeout=1.0) as response:
            self.assertIn('<span class="brand">Workbench</span>', response.read().decode("utf-8"))
        for link in loads:
            with self.subTest(link=link):
                with urlopen(urljoin(page_url, link), timeout=1.0) as response:
                    self.assertEqual(response.status, 200)
        for route in ("v2", "v2/index.html", "v2/shots/0-start.png", "v2/js/missing.js", "shared/missing.js"):
            with self.subTest(route=route):
                with self.assertRaises(HTTPError) as caught:
                    urlopen(f"{self.server.url}{route}", timeout=1.0)
                self.assertEqual(caught.exception.code, 404)

    def test_only_created_hosts_are_listed_with_their_status_state(self) -> None:
        runner = {"kind": "host", "host_name": "chase-sim-runner", "url": closed_url(), "pid": os.getpid()}
        with (
            tempfile.TemporaryDirectory() as tmp,
            fake_metrics_ui() as simulator,
            mock.patch.dict(os.environ, {CHASE_UI_WS_URL_ENV: simulator.url}),
            chase_runtime(Path(tmp)),
            MetricsUiPresence(simulator.url, runner, retry_s=0.05),
        ):
            wait_until(MetricsUiWsClient(simulator.url).list_agents, message="the runner never attached")
            payload = json.loads(_read_text(f"{self.server.url}api/hosts"))

        self.assertEqual(payload["hosts"], [
            {"host_name": "chase-sim-chaser", "state": "not started"},
        ])

class ViewerServerTests(unittest.TestCase):
    def test_workbench_is_created_on_first_visit_and_closed_with_the_viewer(self) -> None:
        runner = ImageReplayRunner(cadence_ms=0, step_factories={"perception": FixtureMapper})
        self.addCleanup(runner.close)
        with mock.patch("cli.automa_cli.hosts.views.runtime_view.ImageReplayRunner", return_value=runner) as build:
            server = runtime_view_server(host_name="test-vehicle", port=0).start()
            self.addCleanup(server.stop)
            _read_text(server.url)
            _read_text(f"{server.url}api/health")
            build.assert_not_called()

            for _ in range(2):
                state = json.loads(_read_text(f"{server.workbench_url}api/state"))
                self.assertEqual(state["phase"], "idle")
                self.assertIsNone(state["source_identity"])
            build.assert_called_once_with()
            with mock.patch.object(runner, "close", wraps=runner.close) as close:
                server.stop()
                close.assert_called_once_with()

    def test_server_without_a_host_list_lists_no_hosts(self) -> None:
        server = RuntimeViewServer(
            host_name="viewer", port=0, replay_runner_factory=ImageReplayRunner,
        ).start()
        self.addCleanup(server.stop)

        self.assertEqual(json.loads(_read_text(f"{server.url}api/hosts")), {"hosts": []})

    def test_a_read_route_is_served_only_by_a_view_built_with_it(self) -> None:
        document = {"schema": "automa_simulators_status", "simulators": [{"url": "http://127.0.0.1:5050/"}]}
        served = RuntimeViewServer(
            host_name="viewer", port=0, reads={"/api/simulators": lambda query: document}, replay_runner_factory=ImageReplayRunner,
        ).start()
        self.addCleanup(served.stop)
        unserved = RuntimeViewServer(
            host_name="viewer", port=0, replay_runner_factory=ImageReplayRunner,
        ).start()
        self.addCleanup(unserved.stop)

        self.assertEqual(json.loads(_read_text(f"{served.url}api/simulators")), document)
        with self.assertRaises(HTTPError) as caught:
            urlopen(f"{unserved.url}api/simulators", timeout=1.0)
        self.assertEqual(caught.exception.code, 404)

    def test_a_read_takes_the_query_and_answers_a_document_an_image_or_a_refusal(self) -> None:
        def frame(query):
            if query.get("v") == "gone":
                return {"ok": False, "error": "That frame's image is no longer available."}
            if query.get("v"):
                return b"jpeg", "image/jpeg"
            raise ValueError("v must be a non-empty string")

        served = RuntimeViewServer(
            host_name="viewer", port=0, reads={"/api/query": lambda query: {"query": dict(query)}, "/api/frame": frame},
            replay_runner_factory=ImageReplayRunner,
        ).start()
        self.addCleanup(served.stop)

        self.assertEqual(json.loads(_read_text(f"{served.url}api/query?host_name=probe&step=plan&step=memory")),
                         {"query": {"host_name": "probe", "step": "plan"}})
        with urlopen(f"{served.url}api/frame?v=chase_frame_1", timeout=1.0) as response:
            self.assertEqual((response.status, response.headers["Content-Type"], response.read()),
                             (200, "image/jpeg", b"jpeg"))
        for query, error in [("v=gone", "That frame's image is no longer available."),
                             ("", "v must be a non-empty string")]:
            with self.subTest(query=query), self.assertRaises(HTTPError) as caught:
                urlopen(f"{served.url}api/frame?{query}", timeout=1.0)
            self.assertEqual((caught.exception.code, json.loads(caught.exception.read())), (400, {"ok": False, "error": error}))

    def test_server_refuses_to_start_without_a_workbench_runner(self) -> None:
        with self.assertRaisesRegex(TypeError, "replay_runner or replay_runner_factory"):
            RuntimeViewServer(host_name="viewer", port=0)


class ViewerWorkbenchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = ImageReplayRunner(cadence_ms=0, step_factories={"perception": FixtureMapper})
        self.addCleanup(self.runner.close)
        self.server = RuntimeViewServer(
            host_name="viewer",
            port=0,
            replay_runner=self.runner,
        ).start()
        self.addCleanup(self.server.stop)
        self.base = self.server.workbench_url

    def test_workbench_page_and_its_files_are_served_under_the_prefix(self) -> None:
        with urlopen(self.base, timeout=1.0) as response:
            self.assertEqual(response.headers["Content-Security-Policy"], WORKBENCH_CONTENT_SECURITY_POLICY)
            page = response.read().decode("utf-8")
        self.assertIn("no source selected", page)
        home = re.search(r'<a class="automa-home" href="([^"]+)"', page).group(1)
        with urlopen(urljoin(self.base, home), timeout=1.0) as response:
            self.assertIn('id="workbenchLink"', response.read().decode("utf-8"))  # the v2 page, which links back
        for route in ("workbench", "workbench/api/health", "workbench/static/missing.js"):
            with self.subTest(route=route):
                with self.assertRaises(HTTPError) as caught:
                    urlopen(f"{self.server.url}{route}", timeout=1.0)
                self.assertEqual(caught.exception.code, 404)

    def test_empty_workbench_replays_a_directory_typed_into_it(self) -> None:
        idle = json.loads(_read_text(f"{self.base}api/state"))
        self.assertEqual(idle["phase"], "idle")
        self.assertIsNone(idle["source_identity"])

        with image_source(1) as root:
            started = post_action(self.base, {"action": "start", "source_dir": str(root), "cadence_ms": 0})
            self.assertEqual(self.runner.wait(5)["phase"], "completed")
            query = urlencode({"run_id": started["state"]["run_id"], "position": "0"})
            with urlopen(f"{self.base}api/frame?{query}", timeout=1.0) as frame:
                self.assertEqual(frame.status, 200)
                self.assertTrue(frame.read())


class OpenViewerCommandTests(unittest.TestCase):
    def test_automa_without_a_command_serves_the_viewer(self) -> None:
        with mock.patch.object(app, "serve_viewer", return_value=130) as serve_viewer:
            self.assertEqual(app.main([]), 130)
        serve_viewer.assert_called_once()
        self.assertTrue(callable(serve_viewer.call_args.kwargs["emit"]))

    def test_the_viewer_opens_the_v2_page(self) -> None:
        server = mock.Mock(url="http://127.0.0.1:8473/")
        with (mock.patch.object(runtime_view, "running_viewer_url", return_value=None),
              mock.patch.object(runtime_view, "runtime_view_server", return_value=mock.Mock(start=lambda: server)),
              mock.patch.object(runtime_view, "launch_browser") as launch,
              mock.patch.object(runtime_view.time, "sleep", side_effect=KeyboardInterrupt)):
            self.assertEqual(runtime_view.serve_viewer(), 130)
            self.assertEqual(runtime_view.serve_viewer(page="/v2/?host=car"), 130)
        self.assertEqual([call.args[0] for call in launch.call_args_list],
                         ["http://127.0.0.1:8473/v2/", "http://127.0.0.1:8473/v2/?host=car"])
        self.assertEqual(server.stop.call_count, 2)

    def test_a_viewer_already_serving_is_opened_rather_than_served_again(self) -> None:
        running = runtime_view_server(host_name="viewer", port=0).start()
        self.addCleanup(running.stop)
        with (mock.patch("cli.automa_cli.frontend.server.VIEWER_PORT", int(running.url.rsplit(":", 1)[1].rstrip("/"))),
              mock.patch.object(runtime_view, "runtime_view_server") as build,
              mock.patch.object(runtime_view, "launch_browser") as launch):
            self.assertEqual(runtime_view.serve_viewer(page="/v2/?host=car"), 0)
        build.assert_not_called()
        launch.assert_called_once_with(f"{running.url}v2/?host=car")

    def test_the_viewer_answers_the_vehicles_simulators_hosts_and_plugins_commands(self) -> None:
        with mock.patch.object(app, "serve_viewer", return_value=130) as serve_viewer:
            app.main([])
        self.assertEqual(sorted(serve_viewer.call_args.kwargs["reads"]), [
            "/api/captures", "/api/captures/frame", "/api/captures/image", "/api/captures/metadata",
            "/api/hosts/frame", "/api/hosts/latest", "/api/hosts/learning", "/api/hosts/status", "/api/plugins/list", "/api/plugins/stream",
            "/api/simulators", "/api/vehicles",
        ])
        self.assertEqual(sorted(serve_viewer.call_args.kwargs["writes"]), [
            "/api/hosts/create", "/api/hosts/delete", "/api/hosts/restart", "/api/hosts/run", "/api/hosts/stop",
            "/api/hosts/update", "/api/plugins/arm", "/api/plugins/disarm", "/api/plugins/save", "/api/plugins/upload",
            "/api/simulators/create", "/api/simulators/delete", "/api/vehicles/discover", "/api/vehicles/remove",
        ])


def _read_text(url: str) -> str:
    with urlopen(url, timeout=1.0) as response:
        return response.read().decode("utf-8")


if __name__ == "__main__":
    unittest.main()
