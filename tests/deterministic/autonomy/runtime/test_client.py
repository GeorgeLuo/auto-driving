"""The CLI's HTTP client for the shared runtime routes."""

from __future__ import annotations

import threading
import unittest

from autonomy.runtime.client import RuntimeClient
from implementations.runtime.chase_sim.service import RuntimeHTTPServer
from tests.deterministic.autonomy.runtime.test_routes import _routes


class RuntimeClientTests(unittest.TestCase):
    def test_an_error_response_returns_its_reason_and_a_closed_host_names_the_request(self) -> None:
        server = RuntimeHTTPServer(("127.0.0.1", 0), _routes()[1])
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        client = RuntimeClient(f"http://127.0.0.1:{server.server_address[1]}", timeout_s=3.0)
        try:
            # A host without a memory step answers 503 with why.
            self.assertEqual(client.reset_memory()["status"], "absent")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(3.0)

        with self.assertRaisesRegex(RuntimeError, r"^POST http://127\.0\.0\.1:\d+/autonomy/memory/reset failed: "):
            client.reset_memory()


if __name__ == "__main__":
    unittest.main()
