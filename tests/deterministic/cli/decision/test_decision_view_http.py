from __future__ import annotations
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from cli.automa_cli.frontend.server import RuntimeViewServer
from cli.automa_cli.workbench.runner import ImageReplayRunner
from tests.deterministic.cli.decision.live_runtime_decision_view_fixtures import (
    LiveRuntimeDecisionViewFixture,
)


class LiveRuntimeDecisionViewTests(LiveRuntimeDecisionViewFixture, unittest.TestCase):
    def test_rejects_write_method_without_decision_dispatch(self) -> None:
        self._publish_exact_transaction()

        for method in ("POST", "PUT", "PATCH", "DELETE"):
            with self.subTest(method=method), self.assertRaises(HTTPError) as write:
                urlopen(Request(f"{self.server.url}api/latest", data=b"{}", method=method), timeout=1)
            self.assertEqual(write.exception.code, 405)

    def test_removed_decision_routes_are_not_an_alternate_publication(self):
        self._publish_exact_transaction()
        for route in ("api/decision/latest", "api/decision/image"):
            with self.subTest(route=route), self.assertRaises(HTTPError) as rejected:
                urlopen(f"{self.server.url}{route}", timeout=1)
            self.assertEqual(rejected.exception.code, 404)

    def test_rejects_non_loopback_runtime_binding_before_start(self) -> None:
        automation_dir = Path(self._temporary.name) / "non-loopback-automation"
        for host in ("0.0.0.0", "192.0.2.1"):
            with self.subTest(host=host):
                with self.assertRaisesRegex(ValueError, "loopback"):
                    RuntimeViewServer(
                        host_name="chase-sim-chaser",
                        automation_dir=automation_dir,
                        host=host,
                        port=0,
                        replay_runner_factory=ImageReplayRunner,
                    )
        self.assertFalse(automation_dir.exists())
