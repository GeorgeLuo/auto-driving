from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from cli.automa_cli.memory_runs import reset_vehicle_memory
from tests.support.cli_runner import run_automa
from tests.support.memory_fixtures import TWO_PLUGIN_IDS, two_plugin_runner

# Mirrors start_automation launcher argv, so the liveness check accepts the worker.
AUTOMATION_COMMAND = (
    "/usr/bin/python3 /repo/cli/automa vehicles automation run "
    "--id chase-sim-chaser --timeout-s 0 --interval-s 0.1 --num-decisions 0 --foreground "
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

    def test_reset_posts_to_the_vehicle_runtime_host(self) -> None:
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
            "cli.automa_cli.memory_runs.runtime_base_url", return_value="http://piracer.test:8887",
        ) as base_url, mock.patch("cli.automa_cli.memory_runs.RuntimeClient") as client:
            client.return_value.reset_memory.return_value = {
                "ok": True,
                "status": "reset",
                "memory": {"plugins": after["plugins"], "reset_count": 2},
            }
            result = reset_vehicle_memory(vehicle_id="piracer", json_output=True)

        base_url.assert_called_once_with("piracer")
        client.assert_called_once_with("http://piracer.test:8887", timeout_s=3.0)

        self.assertEqual(result.exit_code, 0)
        payload = json.loads(result.message)
        self.assertEqual(payload["schema"], "vehicle_memory_reset_v1")
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["confirmed_empty"])
        self.assertEqual(payload["nonempty_plugin_ids"], [])
        self.assertEqual(payload["after"]["plugins"][0]["record_count"], 0)

    def test_two_plugin_reset_confirms_when_every_ledger_is_empty(self) -> None:
        # The host resets a real two-plugin runner when the CLI posts, and the
        # run record republishes its status; the probes read that status.
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

            def reset_memory() -> dict:
                runner.reset(shared)
                publish_state()
                return {"ok": True, "status": "reset", "report": runner.report(), "memory": runner.status()}

            with mock.patch(
                "cli.automa_cli.memory_runs.discover_active_vehicles",
                return_value={"vehicles": [vehicle]},
            ), mock.patch(
                "cli.automa_cli.memory_runs.find_vehicle_by_id",
                return_value=(vehicle, None),
            ), mock.patch(
                "cli.automa_cli.memory_runs.runtime_base_url", return_value="http://127.0.0.1:1"
            ), mock.patch("cli.automa_cli.memory_runs.RuntimeClient") as client, mock.patch(
                "cli.automa_cli.chase_observation.chase_automation_dir", return_value=automation_dir
            ), mock.patch(
                "cli.automa_cli.chase_observation._pid_alive", return_value=True
            ), mock.patch(
                "cli.automa_cli.chase_observation._process_command", return_value=AUTOMATION_COMMAND
            ), mock.patch(
                "cli.automa_cli.streaming.time.time", return_value=now / 1000.0
            ):
                client.return_value.reset_memory.side_effect = reset_memory
                result = reset_vehicle_memory(vehicle_id="chase-sim-chaser", wait_s=2.0)

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
                "cli.automa_cli.memory_runs.runtime_base_url", return_value="http://127.0.0.1:1"
            ), mock.patch("cli.automa_cli.memory_runs.RuntimeClient") as client:
                client.return_value.reset_memory.return_value = {"ok": True, "status": "reset"}
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
