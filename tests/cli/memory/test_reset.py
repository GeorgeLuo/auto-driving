from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from cli.automa_cli.memory_runs import _reset_chase_memory, reset_vehicle_memory
from tests.support.cli_runner import run_automa
from tests.support.memory_fixtures import TWO_PLUGIN_IDS, two_plugin_runner

# Mirrors start_automation launcher argv, so the liveness check accepts the worker.
AUTOMATION_COMMAND = (
    "/usr/bin/python3 /repo/cli/automa vehicles automation run "
    "--id chase-sim-chaser --timeout-s 0 --interval-s 0.1 --frames 0 --foreground "
    "--observe-only"
)


def _ledger(plugin_id: str, *, health: str | None, epoch_id: str, record_count: int) -> dict:
    return {
        "plugin_id": plugin_id,
        "health": health,
        "epoch_id": epoch_id,
        "record_count": record_count,
    }


def _probe(
    vehicle_id: str,
    *ledgers: dict,
    publisher: str | None = "bounded_evidence",
    reset_count: int = 1,
) -> dict:
    """A live ``vehicle_memory_live_v1`` probe listing ``ledgers``."""

    return {
        "schema": "vehicle_memory_live_v1",
        "vehicle_id": vehicle_id,
        "status": "live",
        "plugin_ids": [ledger["plugin_id"] for ledger in ledgers],
        "plugins": list(ledgers),
        "evidence_publisher": publisher,
        "reset_count": reset_count,
    }


class MemoryResetCommandTests(unittest.TestCase):
    def test_memory_reset_help_is_registered(self) -> None:
        result = run_automa("vehicles", "memory", "help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("reset", result.stdout)

    def test_memory_reset_parser_accepts_id(self) -> None:
        result = run_automa("vehicles", "memory", "reset", "--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("--id", result.stdout)
        self.assertIn("every applied plugin's ledger is empty", " ".join(result.stdout.split()))

    def test_reset_vehicle_memory_physical_success(self) -> None:
        vehicle = {
            "vehicle_id": "piracer",
            "provider": "picar",
            "connection": {"base_url": "http://piracer.test:8887"},
        }
        before = _probe(
            "piracer",
            _ledger("bounded_evidence", health="healthy", epoch_id="epoch-a", record_count=4),
        )
        after = _probe(
            "piracer",
            _ledger("bounded_evidence", health="empty", epoch_id="epoch-b", record_count=0),
            reset_count=2,
        )
        discovery = {"vehicles": [vehicle]}
        with mock.patch(
            "cli.automa_cli.memory_runs.discover_active_vehicles",
            return_value=discovery,
        ), mock.patch(
            "cli.automa_cli.memory_runs.find_vehicle_by_id",
            return_value=(vehicle, None),
        ), mock.patch(
            "cli.automa_cli.memory_runs.probe_live_memory",
            side_effect=[before, after],
        ), mock.patch(
            "cli.automa_cli.memory_runs.post_memory_reset",
            return_value={
                "ok": True,
                "status": "reset",
                "http_status": 200,
                "memory": {"plugins": after["plugins"], "reset_count": 2},
            },
        ):
            result = reset_vehicle_memory(vehicle_id="piracer", json_output=True)

        self.assertEqual(result.exit_code, 0)
        payload = json.loads(result.message)
        self.assertEqual(payload["schema"], "vehicle_memory_reset_v1")
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["confirmed_empty"])
        self.assertEqual(payload["nonempty_plugin_ids"], [])
        self.assertEqual(payload["after"]["plugins"][0]["record_count"], 0)

    def test_reset_vehicle_memory_chase_file_protocol(self) -> None:
        vehicle = {
            "vehicle_id": "chase-sim-chaser",
            "provider": "chase-sim",
        }
        before = _probe(
            "chase-sim-chaser",
            _ledger("bounded_evidence", health="healthy", epoch_id="epoch-1", record_count=3),
        )
        after = _probe(
            "chase-sim-chaser",
            _ledger("bounded_evidence", health="empty", epoch_id="epoch-2", record_count=0),
            reset_count=2,
        )
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
                                    "memory": {"plugins": after["plugins"], "reset_count": 2},
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
                "cli.automa_cli.memory_runs.discover_active_vehicles",
                return_value={"vehicles": [vehicle]},
            ), mock.patch(
                "cli.automa_cli.memory_runs.find_vehicle_by_id",
                return_value=(vehicle, None),
            ), mock.patch(
                "cli.automa_cli.memory_runs.probe_live_memory",
                side_effect=[before, after, after],
            ), mock.patch(
                "cli.automa_cli.memory_runs._automation_dir",
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

    def test_two_plugin_reset_confirms_when_every_ledger_is_empty(self) -> None:
        # The worker resets a real two-plugin runner and republishes its status;
        # the probes read that status from state.json.
        runner, shared = two_plugin_runner()
        now = 1_700_000_000_000
        vehicle = {"vehicle_id": "chase-sim-chaser", "provider": "chase-sim"}
        with tempfile.TemporaryDirectory() as tmp:
            automation_dir = Path(tmp) / "automation"
            automation_dir.mkdir(parents=True)
            state_path = automation_dir / "state.json"

            def publish_state() -> None:
                state_path.write_text(
                    json.dumps(
                        {
                            "status": "running",
                            "pid": 424242,
                            "updated_at_ms": now,
                            "memory": {"activation": None, "status": runner.status()},
                        }
                    ),
                    encoding="utf-8",
                )

            publish_state()

            def fake_worker() -> None:
                request_path = automation_dir / "memory_reset.request.json"
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline:
                    if request_path.exists():
                        request = json.loads(request_path.read_text(encoding="utf-8"))
                        runner.reset(shared)
                        report = runner.report()
                        publish_state()
                        (automation_dir / "memory_reset.result.json").write_text(
                            json.dumps(
                                {
                                    "schema": "automa_memory_reset_result_v0",
                                    "ok": True,
                                    "status": "reset",
                                    "token": request["token"],
                                    "report": report,
                                    "memory": runner.status(),
                                }
                            ),
                            encoding="utf-8",
                        )
                        return
                    time.sleep(0.01)

            worker = threading.Thread(target=fake_worker, daemon=True)
            worker.start()
            with mock.patch(
                "cli.automa_cli.memory_runs.discover_active_vehicles",
                return_value={"vehicles": [vehicle]},
            ), mock.patch(
                "cli.automa_cli.memory_runs.find_vehicle_by_id",
                return_value=(vehicle, None),
            ), mock.patch(
                "cli.automa_cli.memory_runs._automation_dir", return_value=automation_dir
            ), mock.patch(
                "cli.automa_cli.streaming._automation_dir", return_value=automation_dir
            ), mock.patch(
                "cli.automa_cli.automation._pid_alive", return_value=True
            ), mock.patch(
                "cli.automa_cli.automation._process_command", return_value=AUTOMATION_COMMAND
            ), mock.patch(
                "cli.automa_cli.memory_runs.time.time", return_value=now / 1000.0
            ), mock.patch(
                "cli.automa_cli.streaming.time.time", return_value=now / 1000.0
            ):
                result = reset_vehicle_memory(vehicle_id="chase-sim-chaser", wait_s=2.0)
            worker.join(timeout=2.0)

        self.assertEqual(result.exit_code, 0, result.message)
        lines = result.message.splitlines()
        self.assertIn("Plugins: bounded_evidence, recording_test", lines)
        self.assertRegex(
            result.message,
            r"  bounded_evidence: epoch \S+ -> \S+, records 1 -> 0, health healthy -> empty",
        )
        self.assertIn(
            "  recording_test: epoch epoch-1 -> epoch-2, records 1 -> 0, health None -> None",
            lines,
        )
        self.assertIn("Evidence publisher: bounded_evidence -> bounded_evidence", lines)
        self.assertNotIn("Warning", result.message)

    def test_reset_is_unconfirmed_while_any_plugin_holds_records(self) -> None:
        # The first plugin keeps its records; reading only the last plugin
        # would have confirmed this reset.
        vehicle = {"vehicle_id": "chase-sim-chaser", "provider": "chase-sim"}
        before = _probe(
            "chase-sim-chaser",
            _ledger("bounded_evidence", health="healthy", epoch_id="epoch-1", record_count=3),
            _ledger("recording_test", health=None, epoch_id="epoch-1", record_count=1),
        )
        after = _probe(
            "chase-sim-chaser",
            _ledger("bounded_evidence", health="healthy", epoch_id="epoch-1", record_count=3),
            _ledger("recording_test", health=None, epoch_id="epoch-2", record_count=0),
            reset_count=2,
        )
        self.assertEqual(TWO_PLUGIN_IDS, ("bounded_evidence", "recording_test"))
        for json_output in (True, False):
            with self.subTest(json_output=json_output), mock.patch(
                "cli.automa_cli.memory_runs.discover_active_vehicles",
                return_value={"vehicles": [vehicle]},
            ), mock.patch(
                "cli.automa_cli.memory_runs.find_vehicle_by_id",
                return_value=(vehicle, None),
            ), mock.patch(
                "cli.automa_cli.memory_runs.probe_live_memory",
                side_effect=[before, after],
            ), mock.patch(
                "cli.automa_cli.memory_runs._reset_chase_memory",
                return_value={"ok": True, "status": "reset"},
            ):
                result = reset_vehicle_memory(
                    vehicle_id="chase-sim-chaser", json_output=json_output
                )
            self.assertEqual(result.exit_code, 2)
            if json_output:
                payload = json.loads(result.message)
                self.assertFalse(payload["confirmed_empty"])
                self.assertEqual(payload["nonempty_plugin_ids"], ["bounded_evidence"])
            else:
                self.assertIn(
                    "Warning: live probe did not confirm an empty memory after reset. "
                    "Still holding records: bounded_evidence.",
                    result.message,
                )

    def test_chase_reset_ack_is_not_blocked_by_vehicle_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            automation_dir = Path(tmp) / "automation"
            automation_dir.mkdir(parents=True)

            def fake_worker() -> None:
                request_path = automation_dir / "memory_reset.request.json"
                deadline = time.monotonic() + 1.0
                while time.monotonic() < deadline:
                    if request_path.exists():
                        request = json.loads(request_path.read_text(encoding="utf-8"))
                        (automation_dir / "memory_reset.result.json").write_text(
                            json.dumps(
                                {
                                    "schema": "automa_memory_reset_result_v0",
                                    "ok": True,
                                    "status": "reset",
                                    "token": request["token"],
                                }
                            ),
                            encoding="utf-8",
                        )
                        return
                    time.sleep(0.005)

            unchanged = _probe(
                "chase-sim-chaser",
                _ledger("bounded_evidence", health="healthy", epoch_id="epoch-1", record_count=3),
            )
            worker = threading.Thread(target=fake_worker, daemon=True)
            worker.start()
            with mock.patch(
                "cli.automa_cli.memory_runs._automation_dir",
                return_value=automation_dir,
            ), mock.patch(
                "cli.automa_cli.memory_runs._probe_chase_memory",
                return_value=unchanged,
            ), mock.patch(
                "cli.automa_cli.memory_runs.probe_live_memory",
                side_effect=AssertionError("general discovery must not run inside Chase reset wait"),
            ):
                result = _reset_chase_memory(
                    vehicle_id="chase-sim-chaser",
                    before=unchanged,
                    wait_s=0.5,
                )
            worker.join(timeout=1.0)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "reset")

    def test_chase_reset_fallback_needs_a_new_epoch_and_every_ledger_empty(self) -> None:
        before = _probe(
            "chase-sim-chaser",
            _ledger("bounded_evidence", health="healthy", epoch_id="epoch-1", record_count=3),
            _ledger("recording_test", health=None, epoch_id="epoch-1", record_count=1),
        )
        partly_reset = _probe(
            "chase-sim-chaser",
            _ledger("bounded_evidence", health="healthy", epoch_id="epoch-1", record_count=3),
            _ledger("recording_test", health=None, epoch_id="epoch-2", record_count=0),
        )
        reset = _probe(
            "chase-sim-chaser",
            _ledger("bounded_evidence", health="empty", epoch_id="epoch-2", record_count=0),
            _ledger("recording_test", health=None, epoch_id="epoch-2", record_count=0),
            reset_count=2,
        )
        with tempfile.TemporaryDirectory() as tmp:
            automation_dir = Path(tmp) / "automation"
            automation_dir.mkdir(parents=True)
            with mock.patch(
                "cli.automa_cli.memory_runs._automation_dir", return_value=automation_dir
            ), mock.patch(
                "cli.automa_cli.memory_runs._probe_chase_memory",
                side_effect=[partly_reset, reset],
            ):
                result = _reset_chase_memory(
                    vehicle_id="chase-sim-chaser", before=before, wait_s=1.0
                )

        self.assertEqual(result["detected_via"], "live_probe")
        self.assertEqual(
            [entry["plugin_id"] for entry in result["memory"]["plugins"]], list(TWO_PLUGIN_IDS)
        )
        self.assertEqual(result["memory"]["evidence_publisher"], "bounded_evidence")

    def test_reset_vehicle_memory_absent_is_actionable(self) -> None:
        vehicle = {
            "vehicle_id": "piracer",
            "provider": "picar",
            "connection": {"base_url": "http://piracer.test:8887"},
        }
        absent = {
            "schema": "vehicle_memory_live_v1",
            "vehicle_id": "piracer",
            "status": "absent",
            "error": "No live memory component",
        }
        with mock.patch(
            "cli.automa_cli.memory_runs.discover_active_vehicles",
            return_value={"vehicles": [vehicle]},
        ), mock.patch(
            "cli.automa_cli.memory_runs.find_vehicle_by_id",
            return_value=(vehicle, None),
        ), mock.patch(
            "cli.automa_cli.memory_runs.probe_live_memory",
            return_value=absent,
        ):
            result = reset_vehicle_memory(vehicle_id="piracer")
        self.assertEqual(result.exit_code, 2)
        self.assertIn("No live memory step", result.message)


if __name__ == "__main__":
    unittest.main(verbosity=2)
