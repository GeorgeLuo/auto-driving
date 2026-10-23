from __future__ import annotations

import json
import unittest
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from cli.automa_cli import app
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


class OpenViewerCommandTests(unittest.TestCase):
    def test_automa_without_a_command_serves_the_viewer(self) -> None:
        with mock.patch.object(app, "serve_viewer", return_value=130) as serve_viewer:
            self.assertEqual(app.main([]), 130)
        serve_viewer.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
