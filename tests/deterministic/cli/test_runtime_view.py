from __future__ import annotations

import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from cli.automa_cli import app
from cli.automa_cli.runtime_view import (
    NOT_CONNECTED_ERROR,
    RUNTIME_HOST_ROUTES,
    RuntimeViewServer,
)
from cli.automa_cli.workbench_server import WORKBENCH_CONTENT_SECURITY_POLICY
from implementations.vehicle.chase_sim.defaults import CHASE_UI_WS_URL_ENV
from implementations.vehicle.chase_sim.metrics_ws import MetricsUiWsClient
from implementations.vehicle.chase_sim.presence import MetricsUiPresence
from tests.shared.cli.automation_pipeline.pipeline_fixtures import chase_runtime
from tests.shared.cli.workbench_fixtures import FixtureMapper, ImageReplayRunner, image_source, post_action
from tests.shared.implementations.runtime.wait import wait_until
from tests.support.fake_hosts import closed_url
from tests.support.fake_metrics_ui import fake_metrics_ui


class ViewerWithoutHostTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = RuntimeViewServer(
            host_name="viewer",
            automation_dir=None,
            port=0,
            connected=False,
        ).start()
        self.addCleanup(self.server.stop)

    def test_pages_are_served_without_a_host(self) -> None:
        for route in ("", "perception", "memory", "plugins", "api/health"):
            with self.subTest(route=route):
                with urlopen(f"{self.server.url}{route}", timeout=1.0) as response:
                    self.assertEqual(response.status, 200)

    def test_runtime_host_routes_say_not_connected(self) -> None:
        for route in sorted(RUNTIME_HOST_ROUTES):
            with self.subTest(route=route):
                self.assertNotConnected(Request(f"{self.server.url}{route.lstrip('/')}"))
        self.assertNotConnected(
            Request(f"{self.server.url}api/plugins", data=b"{}", method="POST")
        )

    def test_splash_marks_every_page_unavailable(self) -> None:
        html = _read_text(self.server.url)
        self.assertIn(f'class="status-dot error" title="{NOT_CONNECTED_ERROR}"', html)
        for label in ("Perception", "Memory", "Action", "Plugins"):
            self.assertIn(
                f'<span class="nav unavailable" title="{NOT_CONNECTED_ERROR}">{label}</span>',
                html,
            )
        self.assertNotIn('<a class="nav"', html)

    def test_connect_window_has_a_status_box(self) -> None:
        html = _read_text(self.server.url)
        self.assertIn('<p class="float-frame-status-text" role="status"></p>', html)
        self.assertIn('<div class="float-frame-status-actions"></div>', html)

    def test_shared_frame_files_are_served(self) -> None:
        for name, content_type in (("float.js", "javascript"), ("float.css", "css")):
            with self.subTest(name=name):
                with urlopen(f"{self.server.url}static/shared/{name}", timeout=1.0) as response:
                    self.assertEqual(response.status, 200)
                    self.assertIn(content_type, response.headers["Content-Type"])
        with self.assertRaises(HTTPError) as caught:
            urlopen(f"{self.server.url}static/shared/missing.js", timeout=1.0)
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

    def test_viewer_publishes_no_discovery_record(self) -> None:
        self.assertIsNone(self.server.record_path)
        self.assertIsNone(self.server.describe()["record_path"])

    def assertNotConnected(self, request: Request) -> None:
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=1.0)
        self.assertEqual(caught.exception.code, 503)
        self.assertEqual(
            json.loads(caught.exception.read()),
            {"ok": False, "error": NOT_CONNECTED_ERROR},
        )


class ConnectedSplashTests(unittest.TestCase):
    def test_splash_links_runtime_pages_while_connected(self) -> None:
        server = RuntimeViewServer(host_name="test-vehicle", automation_dir=None, port=0).start()
        self.addCleanup(server.stop)

        html = _read_text(server.url)

        self.assertIn('class="status-dot live"', html)
        for href in ("/perception", "/memory", "/plugins"):
            self.assertIn(f'<a class="nav" href="{href}">', html)
        self.assertIn('<span class="nav unavailable" title="Action view unavailable">Action</span>', html)


class ViewerWorkbenchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = ImageReplayRunner(cadence_ms=0, step_factories={"perception": FixtureMapper})
        self.addCleanup(self.runner.close)
        self.server = RuntimeViewServer(
            host_name="viewer",
            automation_dir=None,
            port=0,
            connected=False,
            replay_runner=self.runner,
        ).start()
        self.addCleanup(self.server.stop)
        self.base = f"{self.server.url}workbench/"

    def test_splash_links_the_workbench_after_plugins_while_disconnected(self) -> None:
        html = _read_text(self.server.url)

        link = '<a class="nav" href="/workbench/">Workbench</a>'
        self.assertIn(link, html)
        self.assertLess(html.index(f'title="{NOT_CONNECTED_ERROR}">Plugins</span>'), html.index(link))

    def test_workbench_page_and_its_files_are_served_under_the_prefix(self) -> None:
        with urlopen(self.base, timeout=1.0) as response:
            self.assertEqual(response.headers["Content-Security-Policy"], WORKBENCH_CONTENT_SECURITY_POLICY)
            page = response.read().decode("utf-8")
        paths = re.findall(r'(?:src|href)="static/([^"]+)"', page)

        self.assertIn("no source selected", page)
        self.assertIn("js/main.js", paths)
        for path in paths:
            with self.subTest(path=path):
                with urlopen(f"{self.base}static/{path}", timeout=1.0) as response:
                    self.assertEqual(response.status, 200)
                    self.assertIn("javascript" if path.endswith(".js") else "css", response.headers["Content-Type"])
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
        serve_viewer.assert_called_once_with()


def _read_text(url: str) -> str:
    with urlopen(url, timeout=1.0) as response:
        return response.read().decode("utf-8")


if __name__ == "__main__":
    unittest.main()
