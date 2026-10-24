"""The Chase adapter: simulator captures into the shared loop behind the shared routes."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from autonomy.runtime.client import RuntimeClient
from implementations.vehicle.chase_sim.metrics_ws import MetricsUiWsClient
from tests.shared.implementations.runtime.wait import wait_until as _wait
from tests.support.fake_metrics_ui import fake_metrics_ui_server
from tests.support.cli_runner import WORKSPACE_ROOT as ROOT


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
                self.assertEqual(RuntimeClient(base_url).status()["session"]["status"], "pre_start")
                record.unlink()
                self.assertEqual(process.wait(timeout=5.0), 0)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()

    def test_the_simulator_lists_the_host_until_it_exits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, fake_metrics_ui_server() as ws_url:
            runtime_dir = Path(tmp) / "runtime"
            record = runtime_dir / "automation" / "host.json"
            process = subprocess.Popen(
                [sys.executable, "-m", "implementations.runtime.chase_sim.service",
                 "--vehicle-id", "chase-sim-chaser", "--ws-url", ws_url,
                 "--runtime-dir", str(runtime_dir), "--port", "0", "--host-record", str(record)],
                cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            simulator = MetricsUiWsClient(ws_url, timeout_s=2.0)
            try:
                _wait(record.exists, timeout_s=10.0, message="the host never wrote its record")
                host = json.loads(record.read_text(encoding="utf-8"))
                [agent] = _wait(simulator.list_agents, message="the host never attached")
                self.assertEqual(agent["client"], {
                    "kind": "vehicle-host", "vehicle_id": "chase-sim-chaser",
                    "url": host["base_url"], "pid": host["pid"],
                })
                record.unlink()
                self.assertEqual(process.wait(timeout=5.0), 0)
                _wait(lambda: simulator.list_agents() == [], message="the exited host stayed listed")
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    unittest.main()
