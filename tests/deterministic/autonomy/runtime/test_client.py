"""The CLI's HTTP client for the shared runtime routes."""

from __future__ import annotations

import re
import threading
import unittest

from autonomy.runtime.client import RuntimeClient
from implementations.runtime.chase_sim.service import RuntimeHTTPServer
from tests.deterministic.autonomy.runtime.test_routes import _routes
from tests.support.fake_hosts import answering


class RuntimeClientTests(unittest.TestCase):
    def test_an_error_response_returns_its_reason_and_a_closed_host_names_the_request(self) -> None:
        server = RuntimeHTTPServer(("127.0.0.1", 0), _routes()[1])
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        client = RuntimeClient(f"http://127.0.0.1:{server.server_address[1]}", timeout_s=3.0)
        try:
            # A host without a decision publication answers 503 with why.
            self.assertEqual(client.request("GET", "/host/decision/latest")["reason"], "missing")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(3.0)

        with self.assertRaisesRegex(RuntimeError, r"^GET http://127\.0\.0\.1:\d+/host/decision/latest failed: "):
            client.request("GET", "/host/decision/latest")

    def test_a_reply_that_is_not_a_json_object_raises_a_runtime_error(self) -> None:
        for body, status, reason in (
            (b"[]", 200, "returned a non-object JSON payload"),
            (b"<html></html>", 200, "returned non-JSON body"),
            (b"[]", 500, "returned HTTP 500"),
        ):
            with self.subTest(body=body, status=status), answering(body, status) as url:
                with self.assertRaisesRegex(RuntimeError, rf"^GET {re.escape(url)}/host/runtime {reason}$"):
                    RuntimeClient(url, timeout_s=3.0).status()


if __name__ == "__main__":
    unittest.main()
