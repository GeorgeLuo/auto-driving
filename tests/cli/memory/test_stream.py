from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from cli.automa_cli.memory import probe_live_memory, stream_vehicle_memory
from tests.support.cli_runner import run_automa


class MemoryStreamTests(unittest.TestCase):
    def test_probe_physical_memory_live(self) -> None:
        vehicle = {
            "vehicle_id": "piracer",
            "provider": "picar",
            "connection": {"base_url": "http://piracer.local:8887"},
        }
        status = {
            "ok": True,
            "drive_mode": "user",
            "autonomy": {
                "last_control": {
                    "metadata": {"has_memory": True},
                },
                "components": {
                    "memory": {
                        "implementation_id": "bounded_evidence",
                        "implementation_spec": (
                            "implementations.memory.bounded_evidence:BoundedEvidenceLedger"
                        ),
                        "last_health": "healthy",
                        "last_epoch_id": "epoch-2",
                        "last_record_count": 7,
                        "update_count": 12,
                        "reset_count": 1,
                        "failure_count": 0,
                        "bounds": {
                            "max_records": 32,
                            "max_age_ms": 10000,
                            "eviction_policy": "oldest_first",
                        },
                    }
                },
            },
        }
        with patch(
            "cli.automa_cli.memory.fetch_autonomy_status",
            return_value=status,
        ):
            live = probe_live_memory(vehicle_id="piracer", vehicle=vehicle)

        self.assertEqual(live["status"], "live")
        self.assertEqual(live["implementation_id"], "bounded_evidence")
        self.assertEqual(live["last_record_count"], 7)
        self.assertTrue(live["has_memory"])

    def test_probe_physical_memory_absent_is_actionable(self) -> None:
        vehicle = {
            "vehicle_id": "piracer",
            "provider": "picar",
            "connection": {"base_url": "http://piracer.local:8887"},
        }
        status = {
            "ok": True,
            "drive_mode": "user",
            "autonomy": {"components": {"perception": {"algorithm": "lightweight_observer"}}},
        }
        with patch(
            "cli.automa_cli.memory.fetch_autonomy_status",
            return_value=status,
        ):
            live = probe_live_memory(vehicle_id="piracer", vehicle=vehicle)
        self.assertEqual(live["status"], "absent")
        self.assertIn("update core", live["error"])

    def test_stream_once_json_uses_discovery(self) -> None:
        vehicle = {
            "vehicle_id": "piracer",
            "provider": "picar",
            "connection": {"base_url": "http://piracer.local:8887"},
            "active": True,
        }
        discovery = {
            "schema": "automa_vehicle_discovery_v0",
            "vehicles": [vehicle],
            "inactive": [],
            "active_count": 1,
        }
        status = {
            "ok": True,
            "drive_mode": "user",
            "autonomy": {
                "components": {
                    "memory": {
                        "implementation_id": "bounded_evidence",
                        "last_health": "healthy",
                        "last_record_count": 3,
                        "update_count": 4,
                        "reset_count": 1,
                        "failure_count": 0,
                    }
                }
            },
        }
        with patch(
            "cli.automa_cli.memory.discover_active_vehicles",
            return_value=discovery,
        ), patch(
            "cli.automa_cli.memory.fetch_autonomy_status",
            return_value=status,
        ):
            result = stream_vehicle_memory(
                vehicle_id="piracer",
                once=True,
                json_output=True,
                output=None,
            )
        self.assertEqual(result.exit_code, 0)
        payload = json.loads(result.message)
        self.assertEqual(payload["status"], "live")
        self.assertEqual(payload["last_record_count"], 3)

    def test_cli_stream_memory_once_help_wired(self) -> None:
        result = run_automa("vehicles", "stream", "help", check=False)
        self.assertEqual(result.returncode, 0)
        self.assertIn("memory", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
