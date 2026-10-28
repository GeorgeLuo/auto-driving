from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cli.automa_cli.hosts.automation import _host_runtime_status
from tests.support.fake_hosts import answering


HOST_NAME = "chase-sim-chaser"


class HostRuntimeStatusTests(unittest.TestCase):
    def read(self, url: str) -> dict:
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "cli.automa_cli.hosts.runtime_hosts.RUNTIME_ROOT", Path(tmp)
        ):
            return _host_runtime_status(HOST_NAME, url, timeout_s=1.0)["process"]

    def test_a_reply_that_is_not_a_host_status_is_a_worker_error(self) -> None:
        for body, reason in (
            (b"[]", "{url} answers but reports no runtime host"),
            (None, "Remote end closed connection without response"),
        ):
            with self.subTest(body=body), answering(body) as url:
                process = self.read(url)

                self.assertEqual((process["status"], process["reason"]), ("error", reason.format(url=url)))

    def test_a_session_error_carries_the_hosts_last_error(self) -> None:
        body = json.dumps({
            "ok": True, "host_name": HOST_NAME, "mode": "autonomy",
            "session": {"status": "error"},
            "host": {"last_error": "Chase passiveCaptureError: session moved"},
        }).encode()
        with answering(body) as url:
            process = self.read(url)

        self.assertEqual(
            (process["status"], process["reason"]),
            ("error", "Chase passiveCaptureError: session moved"),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
