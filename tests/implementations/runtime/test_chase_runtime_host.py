"""The Chase adapter: simulator captures into the shared loop behind the shared routes."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from autonomy.runtime.client import RuntimeClient
from autonomy.runtime.session import RunConfiguration
from cli.automa_cli.bundles import controller_bundle_paths
from implementations.runtime.chase_sim.service import ChaseRuntimeHost
from tests.integration.automation_pipeline.pipeline_fixtures import _FakeCar, _write_activations
from tests.support.fake_metrics_ui import fake_metrics_ui_server

ROOT = Path(__file__).resolve().parents[3]


def _wait(predicate, *, timeout_s: float = 5.0, message: str = ""):
    deadline = time.monotonic() + timeout_s
    while True:
        value = predicate()
        if value:
            return value
        if time.monotonic() > deadline:
            raise AssertionError(message or "condition not reached")
        time.sleep(0.02)


class ChaseRuntimeHostTests(unittest.TestCase):
    def test_a_bounded_session_captures_only_until_its_last_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = controller_bundle_paths(Path(tmp) / "chase-sim-chaser")
            _write_activations(bundle)
            car = _FakeCar()
            with ChaseRuntimeHost(car=car, runtime_dir=Path(bundle["runtime_dir"]),
                                  vehicle_id="chase-sim-chaser") as host:
                client = RuntimeClient(host.base_url, timeout_s=2.0)
                client.start(RunConfiguration(mode="observe_only", interval_s=0.0, num_decisions=2))
                session = _wait(
                    lambda: (s := client.status()["session"])["status"] != "running" and s,
                    message="the session did not complete",
                )
                captured = car.capture_count
                time.sleep(0.2)
                observation = client.host_status()["autonomy"]["components"]["observation"]
                decision = client.request("GET", "/autonomy/decision/latest")

        self.assertEqual((session["status"], session["processed_decisions"]), ("completed", 2))
        self.assertEqual(car.capture_count, captured, "the host captured after its session ended")
        self.assertEqual(observation["latest"]["mode"], "observe_only")
        self.assertFalse(observation["latest"]["control"]["applied"])
        self.assertEqual(decision["status"], "ready", decision)

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
