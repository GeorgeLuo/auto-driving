from __future__ import annotations
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, build_opener, urlopen
from unittest.mock import patch
from cli.automa_cli.runtime_view import RuntimeViewServer
from tests.cli.decision.live_runtime_decision_view_fixtures import (
    LiveRuntimeDecisionViewFixture,
    _NoRedirectHandler,
)


class LiveRuntimeDecisionViewTests(LiveRuntimeDecisionViewFixture, unittest.TestCase):
    def test_rejects_write_method_without_decision_dispatch(self) -> None:
        self._publish_exact_transaction()

        with patch.object(
            self.server.decision,
            "latest_payload",
            side_effect=AssertionError("write request reached the decision publisher"),
        ):
            for method in ("POST", "PUT", "PATCH", "DELETE"):
                with self.subTest(method=method):
                    with self.assertRaises(HTTPError) as write:
                        urlopen(
                            Request(
                                f"{self.server.url}api/decision/latest",
                                data=b"{}",
                                method=method,
                            ),
                            timeout=1.0,
                        )
                    self.assertEqual(write.exception.code, 405)
                    self.assertEqual(
                        write.exception.headers["Cache-Control"], "no-store"
                    )

    def test_rejects_traversal_and_url_shaped_image_ids_without_dispatch(self) -> None:
        generation = self.server.decision.generation_id
        self.assertIsNotNone(generation)
        secret_path = Path(self._temporary.name) / "route-boundary-secret.txt"
        secret = "route-boundary-secret"
        secret_path.write_text(secret, encoding="utf-8")
        base_url = self.server.url.rstrip("/")
        targets = (
            f"{base_url}/api/decision/image/%2e%2e/%2e%2e/{secret_path.name}",
            f"{base_url}/api/decision/image?generation={generation}&id=..%2F..%2F{secret_path.name}",
            f"{base_url}/api/decision/image?generation={generation}&id={quote(secret_path.as_uri(), safe='')}",
        )
        opener = build_opener(_NoRedirectHandler)
        with patch.object(
            self.server.decision,
            "image_response",
            side_effect=AssertionError("malformed image request reached the publisher"),
        ):
            for target in targets:
                with self.subTest(target=target):
                    with self.assertRaises(HTTPError) as rejected:
                        opener.open(Request(target, method="GET"), timeout=1.0)
                    self.assertIn(rejected.exception.code, (400, 404))
                    self.assertEqual(
                        rejected.exception.headers["Cache-Control"], "no-store"
                    )
                    self.assertNotIn("Location", rejected.exception.headers)
                    self.assertNotIn(secret, rejected.exception.read().decode("utf-8"))

    def test_rejects_non_loopback_runtime_binding_before_start(self) -> None:
        automation_dir = Path(self._temporary.name) / "non-loopback-automation"
        for host in ("0.0.0.0", "192.0.2.1"):
            with self.subTest(host=host):
                with self.assertRaisesRegex(ValueError, "loopback"):
                    RuntimeViewServer(
                        vehicle_id="chase-sim-chaser",
                        automation_dir=automation_dir,
                        host=host,
                        port=0,
                    )
        self.assertFalse(automation_dir.exists())
