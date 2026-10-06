from __future__ import annotations

import io
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from autonomy.decision_cycle.memory.runner import MemoryRunner
from autonomy.decision_cycle.steps import decision_steps
from autonomy.runtime.cycle_host import AutonomyCycleHost
from cli.automa_cli.automation import (
    _automation_command_matches_vehicle,
    assess_chase_worker_liveness,
)
from cli.automa_cli.streaming import probe_live_memory, stream_vehicle_memory
from implementations.decision_cycle.catalog import packaged_activation
from tests.support.cli_runner import run_automa
from tests.support.memory_fixtures import TWO_PLUGIN_IDS, two_plugin_runner

# Mirrors start_automation launcher argv: ... automation run --id <vehicle_id> ...
AUTOMATION_COMMAND = (
    "/usr/bin/python3 /repo/cli/automa vehicles automation run "
    "--id chase-sim-chaser --timeout-s 0 --interval-s 0.1 --frames 0 --foreground "
    "--observe-only"
)

# A memory step status as the chase worker writes it; liveness reads none of it.
WORKER_MEMORY_STATUS = {
    "plugins": [
        {"plugin_id": "bounded_evidence", "state": {"health": "healthy", "record_count": 1}}
    ],
    "evidence_publisher": "bounded_evidence",
}


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
                "steps": {
                    "memory": {
                        "plugin_ids": ["bounded_evidence"],
                        "plugins": [
                            {
                                "plugin_id": "bounded_evidence",
                                "state": {
                                    "health": "healthy",
                                    "epoch_id": "epoch-2",
                                    "record_count": 7,
                                    "bounds": {
                                        "max_records": 32,
                                        "max_age_ms": 10000,
                                        "eviction_policy": "oldest_first",
                                    },
                                },
                            }
                        ],
                        "evidence_publisher": "bounded_evidence",
                        "update_count": 12,
                        "reset_count": 1,
                        "failure_count": 0,
                        "plugin_report": {
                            "available_plugin_ids": ["bounded_evidence", "other"],
                            "selected_plugin_ids": ["other"],
                            "applied_plugin_ids": ["bounded_evidence"],
                            "plugins": [
                                {
                                    "plugin_id": "bounded_evidence",
                                    "duration_ms": 3.0,
                                    "error": None,
                                }
                            ],
                        },
                    }
                },
            },
        }
        with patch(
            "cli.automa_cli.streaming.fetch_autonomy_status",
            return_value=status,
        ):
            live = probe_live_memory(vehicle_id="piracer", vehicle=vehicle)

        self.assertEqual(live["status"], "live")
        self.assertEqual(live["plugin_ids"], ["bounded_evidence"])
        self.assertNotIn("plugin_id", live)
        (ledger,) = live["plugins"]
        self.assertEqual(ledger["plugin_id"], "bounded_evidence")
        self.assertEqual(
            {key: ledger[key] for key in ("health", "epoch_id", "record_count")},
            {"health": "healthy", "epoch_id": "epoch-2", "record_count": 7},
        )
        self.assertEqual(ledger["bounds"]["max_records"], 32)
        self.assertEqual(live["evidence_publisher"], "bounded_evidence")
        self.assertTrue(live["has_memory"])
        self.assertEqual(live["plugin_report"]["selected_plugin_ids"], ["other"])
        self.assertEqual(live["plugin_report"]["applied_plugin_ids"], ["bounded_evidence"])

    def test_probe_physical_memory_absent_is_actionable(self) -> None:
        vehicle = {
            "vehicle_id": "piracer",
            "provider": "picar",
            "connection": {"base_url": "http://piracer.local:8887"},
        }
        status = {
            "ok": True,
            "drive_mode": "user",
            "autonomy": {"steps": {"perception": {"plugin_ids": ["floor_continuity"]}, "memory": None}},
        }
        with patch(
            "cli.automa_cli.streaming.fetch_autonomy_status",
            return_value=status,
        ):
            live = probe_live_memory(vehicle_id="piracer", vehicle=vehicle)
        self.assertEqual(live["status"], "absent")
        self.assertIn("update core", live["error"])

    def test_probe_reads_what_the_cycle_host_publishes(self) -> None:
        # The Pi serves ``host.status()`` as ``autonomy``; read it from a real host
        # so the probe and the writer cannot drift apart again.
        vehicle = {
            "vehicle_id": "piracer",
            "provider": "picar",
            "connection": {"base_url": "http://piracer.local:8887"},
        }
        host = AutonomyCycleHost(
            steps=replace(
                decision_steps(),
                memory=MemoryRunner.from_activation(packaged_activation("memory")),
            )
        )
        status = {"ok": True, "drive_mode": "user", "autonomy": host.status()}
        with patch("cli.automa_cli.streaming.fetch_autonomy_status", return_value=status):
            live = probe_live_memory(vehicle_id="piracer", vehicle=vehicle)

        self.assertEqual(live["status"], "live")
        self.assertEqual(live["plugin_ids"], ["bounded_evidence"])
        self.assertEqual(live["selected_plugin_ids"], ["bounded_evidence"])

        status["autonomy"] = AutonomyCycleHost(steps=decision_steps()).status()
        with patch("cli.automa_cli.streaming.fetch_autonomy_status", return_value=status):
            absent = probe_live_memory(vehicle_id="piracer", vehicle=vehicle)
        self.assertEqual(absent["status"], "absent")

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
                "steps": {
                    "memory": {
                        "plugin_ids": ["bounded_evidence"],
                        "plugins": [
                            {
                                "plugin_id": "bounded_evidence",
                                "state": {"health": "healthy", "record_count": 3},
                            }
                        ],
                        "update_count": 4,
                        "reset_count": 1,
                        "failure_count": 0,
                    }
                }
            },
        }
        with patch(
            "cli.automa_cli.streaming.discover_active_vehicles",
            return_value=discovery,
        ), patch(
            "cli.automa_cli.streaming.fetch_autonomy_status",
            return_value=status,
        ):
            result = stream_vehicle_memory(
                vehicle_id="piracer",
                once=True,
                json_output=True,
                output=None,
            )
        self.assertEqual(result.exit_code, 0, result.message)
        payload = json.loads(result.message)
        self.assertEqual(payload["schema"], "vehicle_memory_live_v1")
        self.assertEqual(payload["status"], "live")
        self.assertEqual(payload["vehicle_id"], "piracer")
        self.assertEqual(payload["plugin_ids"], ["bounded_evidence"])
        self.assertEqual(
            [(entry["plugin_id"], entry["record_count"]) for entry in payload["plugins"]],
            [("bounded_evidence", 3)],
        )
        self.assertIsNone(payload["evidence_publisher"])

    def test_chase_stream_once_live_exits_zero(self) -> None:
        now = 1_700_000_000_000
        vehicle = {"vehicle_id": "chase-sim-chaser", "provider": "chase-sim", "active": True}
        discovery = {
            "schema": "automa_vehicle_discovery_v0",
            "vehicles": [vehicle],
            "inactive": [],
            "active_count": 1,
        }
        live_payload = {
            "schema": "vehicle_memory_live_v1",
            "vehicle_id": "chase-sim-chaser",
            "provider": "chase-sim",
            "status": "live",
            "plugins": [{"plugin_id": "bounded_evidence", "record_count": 5}],
            "evidence_publisher": "bounded_evidence",
            "worker_status": "running",
            "probed_at_ms": now,
        }
        with patch(
            "cli.automa_cli.streaming.discover_active_vehicles", return_value=discovery
        ), patch(
            "cli.automa_cli.streaming.probe_live_memory", return_value=live_payload
        ):
            result = stream_vehicle_memory(
                vehicle_id="chase-sim-chaser",
                once=True,
                json_output=True,
                output=None,
            )
        self.assertEqual(result.exit_code, 0, result.message)
        payload = json.loads(result.message)
        self.assertEqual(payload["status"], "live")
        self.assertEqual(payload["plugins"][0]["record_count"], 5)

    def test_chase_stream_once_stale_exits_nonzero(self) -> None:
        vehicle = {"vehicle_id": "chase-sim-chaser", "provider": "chase-sim", "active": True}
        discovery = {
            "schema": "automa_vehicle_discovery_v0",
            "vehicles": [vehicle],
            "inactive": [],
            "active_count": 1,
        }
        stale_payload = {
            "schema": "vehicle_memory_live_v1",
            "vehicle_id": "chase-sim-chaser",
            "provider": "chase-sim",
            "status": "stale",
            "error": "Automation state is stale (age_ms=60000, max_age_ms=30000).",
            "worker_status": "running",
        }
        with patch(
            "cli.automa_cli.streaming.discover_active_vehicles", return_value=discovery
        ), patch(
            "cli.automa_cli.streaming.probe_live_memory", return_value=stale_payload
        ):
            result = stream_vehicle_memory(
                vehicle_id="chase-sim-chaser",
                once=True,
                json_output=True,
                output=None,
            )
        self.assertEqual(result.exit_code, 2)
        payload = json.loads(result.message)
        self.assertEqual(payload["status"], "stale")
        self.assertIn("stale", payload["error"])

    def test_chase_stream_once_stopped_exits_nonzero(self) -> None:
        vehicle = {"vehicle_id": "chase-sim-chaser", "provider": "chase-sim", "active": True}
        discovery = {
            "schema": "automa_vehicle_discovery_v0",
            "vehicles": [vehicle],
            "inactive": [],
            "active_count": 1,
        }
        stopped_payload = {
            "schema": "vehicle_memory_live_v1",
            "vehicle_id": "chase-sim-chaser",
            "provider": "chase-sim",
            "status": "stopped",
            "error": "Automation worker is not running (status=completed).",
            "worker_status": "completed",
        }
        with patch(
            "cli.automa_cli.streaming.discover_active_vehicles", return_value=discovery
        ), patch(
            "cli.automa_cli.streaming.probe_live_memory", return_value=stopped_payload
        ):
            result = stream_vehicle_memory(
                vehicle_id="chase-sim-chaser",
                once=True,
                json_output=True,
                output=None,
            )
        self.assertEqual(result.exit_code, 2)
        payload = json.loads(result.message)
        self.assertEqual(payload["status"], "stopped")
        self.assertIn("not running", payload["error"])

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
                            "status": {
                                "plugins": [
                                    {
                                        "plugin_id": "bounded_evidence",
                                        "state": {
                                            "health": "healthy",
                                            "record_count": 3,
                                            "epoch_id": "epoch-1",
                                        },
                                    }
                                ],
                                "update_count": 9,
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            with patch("cli.automa_cli.memory.RUNTIME_ROOT", runtime_root), patch(
                "cli.automa_cli.streaming._automation_dir",
                return_value=state_path.parent,
            ), patch("cli.automa_cli.streaming.time.time", return_value=now / 1000.0):
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
            "memory": {"status": {**WORKER_MEMORY_STATUS, "update_count": 4}},
        }
        with patch("cli.automa_cli.automation._pid_alive", return_value=False):
            verdict = assess_chase_worker_liveness(
                state=state,
                step="memory",
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
                "status": WORKER_MEMORY_STATUS,
            },
        }
        with patch("cli.automa_cli.automation._pid_alive", return_value=True), patch(
            "cli.automa_cli.automation._process_command", return_value=AUTOMATION_COMMAND
        ):
            verdict = assess_chase_worker_liveness(
                state=state,
                step="memory",
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
                "status": WORKER_MEMORY_STATUS,
            },
        }
        with patch("cli.automa_cli.automation._pid_alive", return_value=True), patch(
            "cli.automa_cli.automation._process_command",
            return_value="python -m other_service --worker",
        ):
            verdict = assess_chase_worker_liveness(
                state=state,
                step="memory",
                probed_at_ms=now,
                max_age_ms=30_000,
                vehicle_id="chase-sim-chaser",
            )
        self.assertFalse(verdict["live"])
        self.assertEqual(verdict["status"], "stale")
        self.assertIn("PID reuse", verdict["error"])

    def test_chase_probe_rejects_vehicle_id_prefix_collision(self) -> None:
        """A worker for chase-sim-chaser-2 must not satisfy probe for chase-sim-chaser."""

        now = 1_700_000_000_000
        longer_id_command = (
            "/usr/bin/python3 /repo/cli/automa vehicles automation run "
            "--id chase-sim-chaser-2 --timeout-s 0 --interval-s 0.1 --frames 0 "
            "--foreground --observe-only"
        )
        state = {
            "status": "running",
            "pid": 424242,
            "updated_at_ms": now - 500,
            "memory": {
                "status": WORKER_MEMORY_STATUS,
            },
        }
        self.assertTrue(
            _automation_command_matches_vehicle(longer_id_command, "chase-sim-chaser-2")
        )
        self.assertFalse(
            _automation_command_matches_vehicle(longer_id_command, "chase-sim-chaser")
        )
        with patch("cli.automa_cli.automation._pid_alive", return_value=True), patch(
            "cli.automa_cli.automation._process_command", return_value=longer_id_command
        ):
            verdict = assess_chase_worker_liveness(
                state=state,
                step="memory",
                probed_at_ms=now,
                max_age_ms=30_000,
                vehicle_id="chase-sim-chaser",
            )
        self.assertFalse(verdict["live"])
        self.assertEqual(verdict["status"], "stale")
        self.assertIn("PID reuse", verdict["error"])

    def test_automation_command_match_requires_exact_id_token(self) -> None:
        exact = (
            "python /repo/cli/automa vehicles automation run --id chase-sim-chaser --foreground"
        )
        self.assertTrue(_automation_command_matches_vehicle(exact, "chase-sim-chaser"))
        self.assertFalse(
            _automation_command_matches_vehicle(exact, "chase-sim-chaser-extra")
        )
        # Substring vehicle_id in a longer --id value must not match.
        prefixed = (
            "python /repo/cli/automa vehicles automation run "
            "--id chase-sim-chaser-extra --foreground"
        )
        self.assertFalse(
            _automation_command_matches_vehicle(prefixed, "chase-sim-chaser")
        )
        # Positional vehicle without --id is not the real launcher shape.
        positional = (
            "python /repo/cli/automa vehicles automation run chase-sim-chaser --foreground"
        )
        self.assertFalse(
            _automation_command_matches_vehicle(positional, "chase-sim-chaser")
        )

    def test_chase_probe_rejects_unavailable_process_identity(self) -> None:
        now = 1_700_000_000_000
        state = {
            "status": "running",
            "pid": 424242,
            "updated_at_ms": now - 500,
            "memory": {
                "status": WORKER_MEMORY_STATUS,
            },
        }
        with patch("cli.automa_cli.automation._pid_alive", return_value=True), patch(
            "cli.automa_cli.automation._process_command", return_value=None
        ):
            verdict = assess_chase_worker_liveness(
                state=state,
                step="memory",
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
                "status": WORKER_MEMORY_STATUS,
            },
        }
        with patch("cli.automa_cli.automation._pid_alive", return_value=True), patch(
            "cli.automa_cli.automation._process_command", return_value=AUTOMATION_COMMAND
        ):
            verdict = assess_chase_worker_liveness(
                state=state,
                step="memory",
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
                "status": WORKER_MEMORY_STATUS,
            },
        }
        with patch("cli.automa_cli.automation._pid_alive", return_value=True), patch(
            "cli.automa_cli.automation._process_command", return_value=AUTOMATION_COMMAND
        ):
            verdict = assess_chase_worker_liveness(
                state=state,
                step="memory",
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
                "status": WORKER_MEMORY_STATUS,
            },
        }
        with patch("cli.automa_cli.automation._pid_alive", return_value=True), patch(
            "cli.automa_cli.automation._process_command", return_value=AUTOMATION_COMMAND
        ):
            verdict = assess_chase_worker_liveness(
                state=state,
                step="memory",
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
                            "status": {
                                "plugins": [
                                    {
                                        "plugin_id": "bounded_evidence",
                                        "state": {
                                            "health": "healthy",
                                            "record_count": 5,
                                            "epoch_id": "epoch-3",
                                        },
                                    }
                                ],
                                "update_count": 12,
                                "reset_count": 1,
                                "failure_count": 0,
                                "plugin_report": {
                                    "selected_plugin_ids": ["other"],
                                    "applied_plugin_ids": ["bounded_evidence"],
                                },
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            with patch("cli.automa_cli.memory.RUNTIME_ROOT", runtime_root), patch(
                "cli.automa_cli.streaming._automation_dir",
                return_value=state_path.parent,
            ), patch("cli.automa_cli.automation._pid_alive", return_value=True), patch(
                "cli.automa_cli.automation._process_command", return_value=AUTOMATION_COMMAND
            ), patch(
                "cli.automa_cli.streaming.time.time", return_value=now / 1000.0
            ):
                live = probe_live_memory(
                    vehicle_id="chase-sim-chaser",
                    vehicle={"vehicle_id": "chase-sim-chaser", "provider": "chase-sim"},
                )
        self.assertEqual(live["status"], "live")
        self.assertEqual(live["plugins"][0]["plugin_id"], "bounded_evidence")
        self.assertEqual(live["plugins"][0]["record_count"], 5)
        self.assertEqual(live["plugins"][0]["epoch_id"], "epoch-3")
        self.assertEqual(live["worker_status"], "running")
        self.assertEqual(live["plugin_report"]["selected_plugin_ids"], ["other"])
        self.assertEqual(live["plugin_report"]["applied_plugin_ids"], ["bounded_evidence"])

    def test_two_plugins_each_show_their_ledger_and_the_publisher(self) -> None:
        runner, _shared = two_plugin_runner()
        status = json.loads(json.dumps(runner.status()))
        now = 1_700_000_000_000
        chase = {"vehicle_id": "chase-sim-chaser", "provider": "chase-sim", "active": True}
        picar = {
            "vehicle_id": "piracer",
            "provider": "picar",
            "connection": {"base_url": "http://piracer.local:8887"},
            "active": True,
        }
        discovery = {
            "schema": "automa_vehicle_discovery_v0",
            "vehicles": [chase, picar],
            "inactive": [],
            "active_count": 2,
        }
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "status": "running",
                        "pid": 424242,
                        "updated_at_ms": now - 500,
                        "memory": {"activation": None, "status": status},
                    }
                ),
                encoding="utf-8",
            )
            probes = {}
            screens = {}
            with patch(
                "cli.automa_cli.streaming.discover_active_vehicles", return_value=discovery
            ), patch(
                "cli.automa_cli.streaming._automation_dir", return_value=state_path.parent
            ), patch("cli.automa_cli.automation._pid_alive", return_value=True), patch(
                "cli.automa_cli.automation._process_command", return_value=AUTOMATION_COMMAND
            ), patch(
                "cli.automa_cli.streaming.time.time", return_value=now / 1000.0
            ), patch(
                "cli.automa_cli.streaming.fetch_autonomy_status",
                return_value={"ok": True, "autonomy": {"steps": {"memory": status}}},
            ):
                for vehicle_id in ("chase-sim-chaser", "piracer"):
                    result = stream_vehicle_memory(
                        vehicle_id=vehicle_id, once=True, json_output=True, output=None
                    )
                    self.assertEqual(result.exit_code, 0, result.message)
                    probes[vehicle_id] = json.loads(result.message)
                screen = io.StringIO()
                result = stream_vehicle_memory(
                    vehicle_id="chase-sim-chaser", once=True, no_clear=True, output=screen
                )
                self.assertEqual(result.exit_code, 0, result.message)
                screens["chase-sim-chaser"] = screen.getvalue()

        for vehicle_id, probe in probes.items():
            with self.subTest(vehicle_id=vehicle_id):
                self.assertEqual(probe["schema"], "vehicle_memory_live_v1")
                ledgers = {entry["plugin_id"]: entry for entry in probe["plugins"]}
                self.assertEqual(list(ledgers), list(TWO_PLUGIN_IDS))
                self.assertEqual(ledgers["bounded_evidence"]["health"], "healthy")
                self.assertEqual(ledgers["bounded_evidence"]["record_count"], 1)
                self.assertEqual(ledgers["bounded_evidence"]["bounds"]["max_records"], 32)
                self.assertIsNone(ledgers["recording_test"]["health"])
                self.assertEqual(ledgers["recording_test"]["record_count"], 1)
                self.assertEqual(ledgers["recording_test"]["epoch_id"], "epoch-1")
                self.assertEqual(ledgers["recording_test"]["update_count"], 1)
                self.assertEqual(probe["evidence_publisher"], "bounded_evidence")

        text = screens["chase-sim-chaser"]
        self.assertIn("Applied plugins: bounded_evidence, recording_test", text)
        self.assertRegex(text, r"  bounded_evidence: health=healthy epoch=\S+ records=1 max_records=32")
        self.assertIn("  recording_test: health=unknown epoch=epoch-1 records=1", text)
        self.assertIn("Evidence publisher: bounded_evidence", text)

    def test_cli_stream_memory_once_help_wired(self) -> None:
        result = run_automa("vehicles", "stream", "help", check=False)
        self.assertEqual(result.returncode, 0)
        self.assertIn("memory", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
