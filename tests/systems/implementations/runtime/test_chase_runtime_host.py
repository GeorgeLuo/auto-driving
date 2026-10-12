"""The Chase adapter: simulator captures into the shared loop behind the shared routes."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from autonomy.runtime.client import RuntimeClient
from tests.shared.implementations.runtime.wait import wait_until as _wait
from tests.support.fake_metrics_ui import fake_metrics_ui_server

ROOT = Path(__file__).resolve().parents[4]


class ChaseRuntimeHostTests(unittest.TestCase):

    def test_the_host_exits_when_its_record_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, fake_metrics_ui_server() as ws_url:
            runtime_dir = Path(tmp) / "runtime"
            record = runtime_dir / "automation" / "host.json"
            process = subprocess.Popen(
                [sys.executable, "-m", "implementations.runtime.chase_sim.service",
                 "--vehicle-id", "chase-sim-chaser", "--ws-url", ws_url,
                 "--runtime-dir", str(runtime_dir), "--port", "0", "--host-record", str(record)],
                cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            try:
                _wait(record.exists, timeout_s=10.0, message="the host never wrote its record")
                base_url = json.loads(record.read_text(encoding="utf-8"))["base_url"]
                self.assertEqual(RuntimeClient(base_url).status()["session"]["status"], "stopped")
                record.unlink()
                self.assertEqual(process.wait(timeout=5.0), 0)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    unittest.main()
