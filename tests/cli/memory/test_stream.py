from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cli.automa_cli.memory import (
    assess_chase_memory_worker_liveness,
    probe_live_memory,
    stream_vehicle_memory,
)
from tests.support.cli_runner import run_automa

AUTOMATION_COMMAND = (
    "python -m cli.automa vehicles automation run chase-sim-chaser --observe-only"
)


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

    def test_chase_probe_rejects_stopped_worker(self) -> None:
        now = 1_700_000_000_000
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            state_path = (
                runtime_root
                / "chase-sim-chaser"
                / "bundle"
                / "runtime"
                / "automation"
                / "state.json"
            )
            state_path.parent.mkdir(parents=True)
            state_path.write_text(
                json.dumps(
                    {
                        "status": "completed",
                        "pid": 424242,
                        "updated_at_ms": now,
                        "memory": {
                            "implementation_id": "bounded_evidence",
                            "status": {
                                "last_health": "healthy",
                                "last_record_count": 3,
                                "last_epoch_id": "epoch-1",
                                "update_count": 9,
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            with patch("cli.automa_cli.memory.RUNTIME_ROOT", runtime_root), patch(
                "cli.automa_cli.memory._automation_dir",
                return_value=state_path.parent,
            ), patch("cli.automa_cli.memory.time.time", return_value=now / 1000.0):
                live = probe_live_memory(
                    vehicle_id="chase-sim-chaser",
                    vehicle={"vehicle_id": "chase-sim-chaser", "provider": "chase-sim"},
                )
        self.assertEqual(live["status"], "stopped")
        self.assertIn("not running", live["error"])

    def test_chase_probe_rejects_dead_pid_as_stale(self) -> None:
        now = 1_700_000_000_000
        state = {
            "status": "running",
            "pid": 424242,
            "updated_at_ms": now,
            "memory": {
                "implementation_id": "bounded_evidence",
                "status": {
                    "last_health": "healthy",
                    "last_record_count": 2,
                    "update_count": 4,
                },
            },
        }
        with patch("cli.automa_cli.memory._pid_alive", return_value=False):
            verdict = assess_chase_memory_worker_liveness(
                state=state,
                probed_at_ms=now,
                max_age_ms=30_000,
                vehicle_id="chase-sim-chaser",
            )
        self.assertFalse(verdict["live"])
        self.assertEqual(verdict["status"], "stale")
        self.assertIn("not running", verdict["error"])

    def test_chase_probe_rejects_stale_publication_age(self) -> None:
        now = 1_700_000_000_000
        state = {
            "status": "running",
            "pid": 424242,
            "updated_at_ms": now - 60_000,
            "memory": {
                "implementation_id": "bounded_evidence",
                "status": {"last_health": "healthy", "last_record_count": 1},
            },
        }
        with patch("cli.automa_cli.memory._pid_alive", return_value=True), patch(
            "cli.automa_cli.memory._process_command", return_value=AUTOMATION_COMMAND
        ):
            verdict = assess_chase_memory_worker_liveness(
                state=state,
                probed_at_ms=now,
                max_age_ms=30_000,
                vehicle_id="chase-sim-chaser",
            )
        self.assertFalse(verdict["live"])
        self.assertEqual(verdict["status"], "stale")
        self.assertIn("stale", verdict["error"])

    def test_chase_probe_rejects_pid_not_matching_automation(self) -> None:
        now = 1_700_000_000_000
        state = {
            "status": "running",
            "pid": 424242,
            "updated_at_ms": now - 500,
            "memory": {
                "implementation_id": "bounded_evidence",
                "status": {"last_health": "healthy", "last_record_count": 1},
            },
        }
        with patch("cli.automa_cli.memory._pid_alive", return_value=True), patch(
            "cli.automa_cli.memory._process_command",
            return_value="python -m other_service --worker",
        ):
            verdict = assess_chase_memory_worker_liveness(
                state=state,
                probed_at_ms=now,
                max_age_ms=30_000,
                vehicle_id="chase-sim-chaser",
            )
        self.assertFalse(verdict["live"])
        self.assertEqual(verdict["status"], "stale")
        self.assertIn("PID reuse", verdict["error"])

    def test_chase_probe_rejects_unavailable_process_identity(self) -> None:
        now = 1_700_000_000_000
        state = {
            "status": "running",
            "pid": 424242,
            "updated_at_ms": now - 500,
            "memory": {
                "implementation_id": "bounded_evidence",
                "status": {"last_health": "healthy", "last_record_count": 1},
            },
        }
        with patch("cli.automa_cli.memory._pid_alive", return_value=True), patch(
            "cli.automa_cli.memory._process_command", return_value=None
        ):
            verdict = assess_chase_memory_worker_liveness(
                state=state,
                probed_at_ms=now,
                max_age_ms=30_000,
                vehicle_id="chase-sim-chaser",
            )
        self.assertFalse(verdict["live"])
        self.assertEqual(verdict["status"], "stale")
        self.assertIn("cannot verify", verdict["error"])

    def test_chase_probe_rejects_missing_vehicle_identity(self) -> None:
        now = 1_700_000_000_000
        state = {
            "status": "running",
            "pid": 424242,
            "updated_at_ms": now - 500,
            "memory": {
                "implementation_id": "bounded_evidence",
                "status": {"last_health": "healthy", "last_record_count": 1},
            },
        }
        with patch("cli.automa_cli.memory._pid_alive", return_value=True), patch(
            "cli.automa_cli.memory._process_command", return_value=AUTOMATION_COMMAND
        ):
            verdict = assess_chase_memory_worker_liveness(
                state=state,
                probed_at_ms=now,
                max_age_ms=30_000,
                vehicle_id=None,
            )
        self.assertFalse(verdict["live"])
        self.assertEqual(verdict["status"], "stale")
        self.assertIn("vehicle_id is required", verdict["error"])

    def test_chase_probe_rejects_future_publication_timestamp(self) -> None:
        now = 1_700_000_000_000
        state = {
            "status": "running",
            "pid": 424242,
            "updated_at_ms": now + 86_400_000,
            "memory": {
                "implementation_id": "bounded_evidence",
                "status": {"last_health": "healthy", "last_record_count": 1},
            },
        }
        with patch("cli.automa_cli.memory._pid_alive", return_value=True), patch(
            "cli.automa_cli.memory._process_command", return_value=AUTOMATION_COMMAND
        ):
            verdict = assess_chase_memory_worker_liveness(
                state=state,
                probed_at_ms=now,
                max_age_ms=30_000,
                vehicle_id="chase-sim-chaser",
                clock_skew_ms=2_000,
            )
        self.assertFalse(verdict["live"])
        self.assertEqual(verdict["status"], "stale")
        self.assertIn("future", verdict["error"])
        self.assertEqual(verdict["age_ms"], -86_400_000)

    def test_chase_probe_allows_small_forward_clock_skew(self) -> None:
        now = 1_700_000_000_000
        state = {
            "status": "running",
            "pid": 424242,
            "updated_at_ms": now + 500,
            "memory": {
                "implementation_id": "bounded_evidence",
                "status": {"last_health": "healthy", "last_record_count": 1},
            },
        }
        with patch("cli.automa_cli.memory._pid_alive", return_value=True), patch(
            "cli.automa_cli.memory._process_command", return_value=AUTOMATION_COMMAND
        ):
            verdict = assess_chase_memory_worker_liveness(
                state=state,
                probed_at_ms=now,
                max_age_ms=30_000,
                vehicle_id="chase-sim-chaser",
                clock_skew_ms=2_000,
            )
        self.assertTrue(verdict["live"])
        self.assertEqual(verdict["age_ms"], 0)

    def test_chase_probe_live_when_running_fresh_and_pid_alive(self) -> None:
        now = 1_700_000_000_000
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            state_path = (
                runtime_root
                / "chase-sim-chaser"
                / "bundle"
                / "runtime"
                / "automation"
                / "state.json"
            )
            state_path.parent.mkdir(parents=True)
            state_path.write_text(
                json.dumps(
                    {
                        "status": "running",
                        "pid": 424242,
                        "updated_at_ms": now - 1_000,
                        "memory": {
                            "implementation_id": "bounded_evidence",
                            "status": {
                                "last_health": "healthy",
                                "last_record_count": 5,
                                "last_epoch_id": "epoch-3",
                                "update_count": 12,
                                "reset_count": 1,
                                "failure_count": 0,
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            with patch("cli.automa_cli.memory.RUNTIME_ROOT", runtime_root), patch(
                "cli.automa_cli.memory._automation_dir",
                return_value=state_path.parent,
            ), patch("cli.automa_cli.memory._pid_alive", return_value=True), patch(
                "cli.automa_cli.memory._process_command", return_value=AUTOMATION_COMMAND
            ), patch(
                "cli.automa_cli.memory.time.time", return_value=now / 1000.0
            ):
                live = probe_live_memory(
                    vehicle_id="chase-sim-chaser",
                    vehicle={"vehicle_id": "chase-sim-chaser", "provider": "chase-sim"},
                )
        self.assertEqual(live["status"], "live")
        self.assertEqual(live["last_record_count"], 5)
        self.assertEqual(live["worker_status"], "running")

    def test_cli_stream_memory_once_help_wired(self) -> None:
        result = run_automa("vehicles", "stream", "help", check=False)
        self.assertEqual(result.returncode, 0)
        self.assertIn("memory", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
