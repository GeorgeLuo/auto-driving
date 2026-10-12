from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from autonomy.decision_cycle.activation import STEPS
from tests.support.cli_runner import run_automa

DISCOVERY = "cli.automa_cli.vehicles.discover_active_vehicles"
PICAR = {
    "vehicle_id": "piracer-test",
    "vehicle_kind": "picar",
    "provider": "picar",
    "connection": {"base_url": "http://piracer-test.local:8887", "source": "test"},
}


class UpdateVehicleTests(unittest.TestCase):
    """Every `vehicles update <step>` stages only for a vehicle it can name."""


    def test_unknown_vehicle_json_is_transferable_between_all_step_commands(self) -> None:
        for step in STEPS:
            with self.subTest(step=step), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                result = run_automa(
                    "vehicles", "update", step, "--id", "parity-unknown",
                    "--timeout-s", "0.05", "--json", runtime_root=root, check=False,
                )
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["schema"], "vehicle_step_update_error_v0")
                self.assertEqual(payload["vehicle_id"], "parity-unknown")
                self.assertEqual(payload["step"], step)
                self.assertEqual(payload["error"], "unknown_vehicle")
                self.assertEqual(result.stderr, "")
                self.assertEqual(list(root.iterdir()), [])


    def test_physical_memory_staging_supports_offline_cli_updates_of_every_step(self) -> None:
        # Exercise the public executable and real discovery HTTP boundary.
        requests = []

        class StatusHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                requests.append(self.path)
                body = b'{"ok": true, "mode": "user"}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args) -> None:
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), StatusHandler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                env = {
                    "PIRACER_ID": "parity-pi",
                    "PIRACER_BASE_URL": f"http://127.0.0.1:{server.server_port}",
                }
                staged = run_automa(
                    "vehicles", "update", "memory", "--id", "parity-pi",
                    "--timeout-s", "0.1", "--json", runtime_root=root, extra_env=env,
                )
                self.assertEqual(json.loads(staged.stdout)["plugins"], ["bounded_evidence"])
                server.shutdown()
                server.server_close()
                worker.join(2)
                before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
                for step in STEPS:
                    with self.subTest(step=step):
                        result = run_automa(
                            "vehicles", "update", step, "--id", "parity-pi",
                            "--timeout-s", "0.1", "--dry-run", "--json",
                            runtime_root=root, extra_env=env,
                        )
                        self.assertTrue(json.loads(result.stdout)["dry_run"])
                self.assertEqual(before, {p: p.read_bytes() for p in root.rglob("*") if p.is_file()})
        finally:
            server.shutdown()
            server.server_close()
            worker.join(2)
        self.assertEqual(requests, ["/autonomy/status"])

    def test_a_slow_host_is_reported_with_the_wait_not_only_as_not_found(self) -> None:
        class SlowHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                time.sleep(1.0)
                self.send_response(200)
                self.end_headers()

            def log_message(self, *_args) -> None:
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), SlowHandler)
        server.daemon_threads = True
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base_url = f"http://127.0.0.1:{server.server_port}"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                result = run_automa(
                    "vehicles", "update", "perception", "--id", "slow-pi", "--timeout-s", "0.2",
                    runtime_root=Path(tmp), extra_env={"PIRACER_ID": "slow-pi", "PIRACER_BASE_URL": base_url},
                    check=False,
                )
        finally:
            server.shutdown()
            server.server_close()
            worker.join(2)
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("Vehicle 'slow-pi' was not found among discoverable vehicles.", result.stdout)
        self.assertIn(f"GET {base_url}/autonomy/status did not answer within 0.2 s", result.stdout)

    def test_help_explains_the_shared_identity_rule(self) -> None:
        for step in STEPS:
            with self.subTest(step=step):
                help_text = " ".join(run_automa("vehicles", "update", step, "--help").stdout.split())
                self.assertIn("matching identity metadata in any staged step", help_text)
                self.assertIn("for each vehicle discovery probe", help_text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
