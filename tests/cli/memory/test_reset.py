from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from cli.automa_cli.memory import reset_vehicle_memory
from tests.support.cli_runner import run_automa


class MemoryResetCommandTests(unittest.TestCase):
    def test_memory_reset_help_is_registered(self) -> None:
        result = run_automa("vehicles", "memory", "help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("reset", result.stdout)

    def test_memory_reset_parser_accepts_id(self) -> None:
        result = run_automa("vehicles", "memory", "reset", "--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("--id", result.stdout)
        self.assertIn("empty epoch", result.stdout.lower())

    def test_reset_vehicle_memory_physical_success(self) -> None:
        vehicle = {
            "vehicle_id": "piracer",
            "provider": "picar",
            "connection": {"base_url": "http://piracer.test:8887"},
        }
        before = {
            "schema": "vehicle_memory_live_v0",
            "vehicle_id": "piracer",
            "provider": "picar",
            "status": "live",
            "implementation_id": "bounded_evidence",
            "last_health": "healthy",
            "last_epoch_id": "epoch-a",
            "last_record_count": 4,
            "reset_count": 1,
        }
        after = {
            **before,
            "last_health": "empty",
            "last_epoch_id": "epoch-b",
            "last_record_count": 0,
            "reset_count": 2,
        }
        discovery = {"vehicles": [vehicle]}
        with mock.patch(
            "cli.automa_cli.memory.discover_active_vehicles",
            return_value=discovery,
        ), mock.patch(
            "cli.automa_cli.memory.find_vehicle_by_id",
            return_value=(vehicle, None),
        ), mock.patch(
            "cli.automa_cli.memory.probe_live_memory",
            side_effect=[before, after],
        ), mock.patch(
            "cli.automa_cli.memory.post_memory_reset",
            return_value={
                "schema": "automa_memory_reset_v0",
                "ok": True,
                "status": "reset",
                "http_status": 200,
                "memory": {
                    "last_health": "empty",
                    "last_epoch_id": "epoch-b",
                    "last_record_count": 0,
                    "reset_count": 2,
                },
            },
        ):
            result = reset_vehicle_memory(vehicle_id="piracer", json_output=True)

        self.assertEqual(result.exit_code, 0)
        payload = json.loads(result.message)
        self.assertEqual(payload["schema"], "vehicle_memory_reset_v0")
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["confirmed_empty"])
        self.assertEqual(payload["after"]["last_record_count"], 0)

    def test_reset_vehicle_memory_chase_file_protocol(self) -> None:
        vehicle = {
            "vehicle_id": "chase-sim-chaser",
            "provider": "chase-sim",
        }
        before = {
            "schema": "vehicle_memory_live_v0",
            "vehicle_id": "chase-sim-chaser",
            "provider": "chase-sim",
            "status": "live",
            "implementation_id": "bounded_evidence",
            "last_health": "healthy",
            "last_epoch_id": "epoch-1",
            "last_record_count": 3,
            "reset_count": 1,
        }
        after = {
            **before,
            "last_health": "empty",
            "last_epoch_id": "epoch-2",
            "last_record_count": 0,
            "reset_count": 2,
        }
        with tempfile.TemporaryDirectory() as tmp:
            automation_dir = Path(tmp) / "automation"
            automation_dir.mkdir(parents=True)

            def fake_worker() -> None:
                deadline = time.time() + 2.0
                request_path = automation_dir / "memory_reset.request.json"
                while time.time() < deadline:
                    if request_path.exists():
                        request = json.loads(request_path.read_text(encoding="utf-8"))
                        result_path = automation_dir / "memory_reset.result.json"
                        result_path.write_text(
                            json.dumps(
                                {
                                    "schema": "automa_memory_reset_result_v0",
                                    "ok": True,
                                    "status": "reset",
                                    "token": request.get("token"),
                                    "memory": {
                                        "last_health": "empty",
                                        "last_epoch_id": "epoch-2",
                                        "last_record_count": 0,
                                        "reset_count": 2,
                                    },
                                }
                            ),
                            encoding="utf-8",
                        )
                        request_path.unlink(missing_ok=True)
                        return
                    time.sleep(0.02)

            worker = threading.Thread(target=fake_worker, daemon=True)
            worker.start()
            with mock.patch(
                "cli.automa_cli.memory.discover_active_vehicles",
                return_value={"vehicles": [vehicle]},
            ), mock.patch(
                "cli.automa_cli.memory.find_vehicle_by_id",
                return_value=(vehicle, None),
            ), mock.patch(
                "cli.automa_cli.memory.probe_live_memory",
                side_effect=[before, after, after],
            ), mock.patch(
                "cli.automa_cli.memory._automation_dir",
                return_value=automation_dir,
            ):
                result = reset_vehicle_memory(
                    vehicle_id="chase-sim-chaser",
                    wait_s=2.0,
                    json_output=True,
                )
            worker.join(timeout=2.0)

        self.assertEqual(result.exit_code, 0)
        payload = json.loads(result.message)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["reset"]["status"], "reset")
        self.assertTrue(payload["confirmed_empty"])

    def test_reset_vehicle_memory_absent_is_actionable(self) -> None:
        vehicle = {
            "vehicle_id": "piracer",
            "provider": "picar",
            "connection": {"base_url": "http://piracer.test:8887"},
        }
        absent = {
            "schema": "vehicle_memory_live_v0",
            "vehicle_id": "piracer",
            "status": "absent",
            "error": "No live memory component",
        }
        with mock.patch(
            "cli.automa_cli.memory.discover_active_vehicles",
            return_value={"vehicles": [vehicle]},
        ), mock.patch(
            "cli.automa_cli.memory.find_vehicle_by_id",
            return_value=(vehicle, None),
        ), mock.patch(
            "cli.automa_cli.memory.probe_live_memory",
            return_value=absent,
        ):
            result = reset_vehicle_memory(vehicle_id="piracer")
        self.assertEqual(result.exit_code, 2)
        self.assertIn("No live memory stage", result.message)


if __name__ == "__main__":
    unittest.main(verbosity=2)
