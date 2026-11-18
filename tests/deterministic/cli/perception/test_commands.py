from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cli.automa_cli.frontend.server import RuntimeViewServer
from cli.automa_cli.workbench.runner import ImageReplayRunner
from tests.shared.cli.invoke import invoke_automa
from tests.support.cli_runner import WORKSPACE_ROOT as ROOT
from tests.support.runtime_fixtures import write_runtime_fixture


class PerceptionCommandTests(unittest.TestCase):
    def test_status_reports_the_hosts_page_on_the_running_viewer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            host_name = "chase-sim-chaser"
            write_runtime_fixture(runtime_root, host_name, pid=os.getpid(), manifest_bundle_root=ROOT)
            server = RuntimeViewServer(host_name="viewer", port=0, replay_runner_factory=ImageReplayRunner).start()
            base, port = server.url, int(server.url.rstrip("/").rsplit(":", 1)[1])
            try:
                with mock.patch("cli.automa_cli.frontend.server.VIEWER_PORT", port):
                    text_result = invoke_automa("hosts", "status", "--long", runtime_root=runtime_root)
                    json_result = invoke_automa("hosts", "status", "--json", runtime_root=runtime_root)
            finally:
                server.stop()

        expected_url = f"{base}v2/?host={host_name}"
        published_view = json.loads(json_result.stdout)["hosts"][0]["run"]["published_view"]
        self.assertEqual(published_view, {"available": True, "url": expected_url})
        self.assertIn(f"\n  view: {expected_url}\n", text_result.stdout)

if __name__ == "__main__":
    unittest.main()
