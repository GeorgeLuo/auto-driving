from __future__ import annotations

import json
import unittest
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from cli.automa_cli import app, runtime_view
from cli.automa_cli.runtime_view import (
    NOT_CONNECTED_ERROR,
    RUNTIME_HOST_ROUTES,
    RuntimeViewServer,
)


class ViewerWithoutHostTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = RuntimeViewServer(
            vehicle_id="viewer",
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

    def test_hosts_are_listed_with_their_readiness(self) -> None:
        status = {
            "vehicles": [
                {"vehicle_id": "chase-sim-chaser", "readiness": {"blocking_layer": None}},
                {"vehicle_id": "chase-sim-runner", "readiness": {"blocking_layer": "simulator_server"}},
            ],
            "other_local_deployments": [{"vehicle_id": "piracer"}],
        }
        with mock.patch.object(runtime_view, "get_vehicle_status", return_value=status):
            payload = json.loads(_read_text(f"{self.server.url}api/hosts"))

        self.assertEqual(payload["hosts"], [
            {"vehicle_id": "chase-sim-chaser", "state": "ready"},
            {"vehicle_id": "chase-sim-runner", "state": "simulator_server"},
            {"vehicle_id": "piracer", "state": None},
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
        server = RuntimeViewServer(vehicle_id="test-vehicle", automation_dir=None, port=0).start()
        self.addCleanup(server.stop)

        html = _read_text(server.url)

        self.assertIn('class="status-dot live"', html)
        for href in ("/perception", "/memory", "/plugins"):
            self.assertIn(f'<a class="nav" href="{href}">', html)
        self.assertIn('<span class="nav unavailable" title="Action view unavailable">Action</span>', html)


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
