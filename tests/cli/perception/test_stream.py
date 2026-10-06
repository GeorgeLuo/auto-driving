from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cli.automa_cli import streaming
from cli.automa_cli.streaming import probe_live_memory
from cli.automa_cli.streaming import probe_live_perception, stream_vehicle_perception
from tests.cli.perception.test_physical_stream import _publication
from tests.support.cli_runner import run_automa

# Mirrors start_automation launcher argv: ... automation run --id <vehicle_id> ...
AUTOMATION_COMMAND = (
    "/usr/bin/python3 /repo/cli/automa vehicles automation run "
    "--id chase-sim-chaser --timeout-s 0 --interval-s 0.1 --frames 0 --foreground "
    "--observe-only"
)
NOW = 1_700_000_000_000
CHASE = {"vehicle_id": "chase-sim-chaser", "provider": "chase-sim"}
PICAR = {
    "vehicle_id": "piracer",
    "provider": "picar",
    "connection": {"base_url": "http://piracer.local:8887"},
}


def _chase_state(**overrides):
    state = {
        "status": "running",
        "pid": 424242,
        "run_id": "run-7",
        "updated_at_ms": NOW - 1_000,
        "frames_processed": 8,
        "perception": {
            "activation": "lightweight_observer",
            "preset": "lightweight_observer",
            "plugins": ["lightweight_observer"],
        },
    }
    state.update(overrides)
    return state


def _chase_record(**overrides):
    record = {
        "frame_id": "chase_frame_000007",
        "frame_index": 7,
        "run_id": "run-7",
        "captured_at_ms": NOW - 700,
        "perception_completed_at_ms": NOW - 400,
        "perception_duration_ms": 300,
        "perception": {
            "schema": "perception_text_v2",
            "status": "ok",
            "lines": ["floor visible"],
            "signals": [{"signal_id": "floor_visible"}],
            "things": [],
        },
        "perception_plugin_report": {"applied_plugin_ids": ["lightweight_observer"]},
    }
    record.update(overrides)
    return record


class PerceptionStreamJsonTests(unittest.TestCase):
    def _stream(self, vehicle, **kwargs):
        buffer = io.StringIO()
        with patch.object(
            streaming,
            "discover_active_vehicles",
            return_value={"active": [vehicle], "inactive": []},
        ), patch.object(streaming, "find_vehicle_by_id", return_value=(vehicle, None)):
            result = stream_vehicle_perception(
                vehicle_id=vehicle["vehicle_id"],
                once=True,
                output=buffer,
                **kwargs,
            )
        return result, buffer.getvalue()

    def _stream_chase(self, automation_dir: Path, *, pid_alive=True, **kwargs):
        with patch.object(streaming, "_automation_dir", return_value=automation_dir), patch(
            "cli.automa_cli.automation._pid_alive", return_value=pid_alive
        ), patch(
            "cli.automa_cli.automation._process_command", return_value=AUTOMATION_COMMAND
        ), patch("cli.automa_cli.streaming.time.time", return_value=NOW / 1000.0):
            return self._stream(CHASE, **kwargs)

    def _write_chase(self, automation_dir: Path, *, state=None, record=None) -> None:
        automation_dir.mkdir(parents=True)
        if state is not None:
            (automation_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
        if record is not None:
            (automation_dir / "latest_perception.json").write_text(
                json.dumps(record), encoding="utf-8"
            )

    def test_picar_json_once_prints_one_live_probe_and_serves_no_page(self) -> None:
        with patch.object(streaming, "fetch_observation_publication", return_value=_publication()), patch.object(
            streaming, "RuntimeViewServer", side_effect=AssertionError("json mode serves no page")
        ), patch.object(
            streaming, "physical_observation_dir", side_effect=AssertionError("json mode writes no frame")
        ):
            result, out = self._stream(PICAR, json_output=True)

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.message, "")
        self.assertNotIn("\033[2J", out)
        lines = out.splitlines()
        self.assertEqual(len(lines), 1)
        live = json.loads(lines[0])
        self.assertEqual(live["schema"], "vehicle_perception_live_v0")
        self.assertEqual(live["provider"], "picar")
        self.assertEqual(live["status"], "live")
        self.assertEqual(live["endpoint"], "http://piracer.local:8887/autonomy/observation/latest")
        self.assertEqual(live["health"], "healthy")
        self.assertEqual(live["frame_id"], "donkey_frame_000011")
        # The Pi reports the result age on its own clock.
        self.assertEqual(live["age_ms"], 120)
        self.assertEqual((live["signal_count"], live["thing_count"]), (1, 1))
        self.assertEqual(live["perception"]["lines"], ["floor visible", "boundary center"])

    def test_picar_json_once_exits_nonzero_unless_perception_is_live(self) -> None:
        cases = {
            "error": {"side_effect": ConnectionError("GET failed: connection refused")},
            "absent": {"return_value": _publication(health="warming", ok=False)},
            "stale": {"return_value": _publication(health="stale")},
        }
        for status, fetch in cases.items():
            with self.subTest(status=status):
                with patch.object(streaming, "fetch_observation_publication", **fetch):
                    result, out = self._stream(PICAR, json_output=True)
                live = json.loads(out)
                self.assertEqual(result.exit_code, 2)
                self.assertEqual(live["status"], status)
                self.assertTrue(live["error"])

        with self.subTest(status="unavailable"):
            # JSON mode reports a missing connection as a probe, not an early exit.
            result, out = self._stream({"vehicle_id": "piracer", "provider": "picar"}, json_output=True)
            self.assertEqual(result.exit_code, 2)
            self.assertEqual(json.loads(out)["status"], "unavailable")

    def test_chase_json_once_reports_this_runs_latest_perception(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            automation_dir = Path(tmp) / "automation"
            self._write_chase(automation_dir, state=_chase_state(), record=_chase_record())
            result, out = self._stream_chase(automation_dir, json_output=True)

        self.assertEqual(result.exit_code, 0)
        live = json.loads(out)
        self.assertEqual(live["status"], "live")
        self.assertEqual(live["provider"], "chase-sim")
        self.assertEqual(live["run_id"], "run-7")
        self.assertEqual(live["worker_status"], "running")
        self.assertEqual(live["frames_processed"], 8)
        self.assertEqual(live["activation"], "lightweight_observer")
        self.assertEqual(live["plugin_ids"], ["lightweight_observer"])
        self.assertEqual(live["plugin_report"]["applied_plugin_ids"], ["lightweight_observer"])
        self.assertEqual(live["frame_id"], "chase_frame_000007")
        self.assertEqual(live["age_ms"], 400)
        self.assertEqual((live["signal_count"], live["thing_count"]), (1, 0))

    def test_chase_json_once_exits_nonzero_unless_this_run_published(self) -> None:
        placeholder = {"schema": "automa_latest_perception_placeholder_v0", "status": "starting"}
        cases = {
            "placeholder": ("absent", {"state": _chase_state(), "record": placeholder}, True),
            "earlier run": ("absent", {"state": _chase_state(), "record": _chase_record(run_id="run-6")}, True),
            "completed": ("stopped", {"state": _chase_state(status="completed"), "record": _chase_record()}, True),
            "dead pid": ("stale", {"state": _chase_state(), "record": _chase_record()}, False),
            "no state": ("unavailable", {}, True),
        }
        for name, (status, files, pid_alive) in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                automation_dir = Path(tmp) / "automation"
                self._write_chase(automation_dir, **files)
                result, out = self._stream_chase(automation_dir, pid_alive=pid_alive, json_output=True)
                live = json.loads(out)
                self.assertEqual(result.exit_code, 2)
                self.assertEqual(live["status"], status)
                self.assertTrue(live["error"])

        with self.subTest("no runtime"), tempfile.TemporaryDirectory() as tmp:
            # JSON mode reports a missing runtime as a probe, not an early exit.
            result, out = self._stream_chase(Path(tmp) / "missing", json_output=True)
            self.assertEqual(result.exit_code, 2)
            self.assertEqual(json.loads(out)["status"], "unavailable")

    def test_chase_terminal_once_exits_nonzero_unless_live(self) -> None:
        for status, exit_code in (("running", 0), ("completed", 2)):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                automation_dir = Path(tmp) / "automation"
                self._write_chase(
                    automation_dir,
                    state=_chase_state(status=status),
                    record=_chase_record(),
                )
                result, out = self._stream_chase(automation_dir, no_clear=True)
                self.assertEqual(result.exit_code, exit_code)
                self.assertEqual(result.message, "")
                self.assertIn("automa perception stream", out)

    def test_chase_worker_verdict_is_shared_with_memory(self) -> None:
        stale = _chase_state(updated_at_ms=NOW - 60_000)
        with tempfile.TemporaryDirectory() as tmp:
            automation_dir = Path(tmp) / "automation"
            self._write_chase(automation_dir, state=stale, record=_chase_record())
            with patch.object(streaming, "_automation_dir", return_value=automation_dir), patch(
                "cli.automa_cli.automation._pid_alive", return_value=True
            ), patch(
                "cli.automa_cli.automation._process_command", return_value=AUTOMATION_COMMAND
            ), patch("cli.automa_cli.streaming.time.time", return_value=NOW / 1000.0):
                perception = probe_live_perception(vehicle_id="chase-sim-chaser", vehicle=CHASE)
                memory = probe_live_memory(vehicle_id="chase-sim-chaser", vehicle=CHASE)

        self.assertEqual(perception["status"], "stale")
        self.assertEqual(memory["status"], "stale")
        self.assertEqual(
            perception["error"].replace("perception", "<step>"),
            memory["error"].replace("memory", "<step>"),
        )

    def test_chase_terminal_shows_the_probe_status_and_reason_for_its_exit(
        self,
    ) -> None:
        cases = (
            (_chase_state(), _chase_record(), True),
            (_chase_state(updated_at_ms=NOW - 60_000), _chase_record(), True),
            (_chase_state(), _chase_record(run_id="previous-run"), True),
            (_chase_state(status="completed"), _chase_record(), True),
            (_chase_state(), _chase_record(), False),
        )
        for state, record, pid_alive in cases:
            with (
                self.subTest(state=state, pid_alive=pid_alive),
                tempfile.TemporaryDirectory() as tmp,
            ):
                path = Path(tmp) / "automation"
                self._write_chase(path, state=state, record=record)
                (path / "process.json").write_text(
                    json.dumps({"pid": 999999}), encoding="utf-8"
                )
                machine, json_text = self._stream_chase(
                    path, pid_alive=pid_alive, json_output=True
                )
                terminal, screen = self._stream_chase(
                    path, pid_alive=pid_alive, no_clear=True
                )
                probe = json.loads(json_text)
                self.assertEqual(terminal.exit_code, machine.exit_code)
                self.assertEqual(terminal.message, "")
                self.assertIn(f"status: {probe['status']}", screen)
                self.assertIn(f"worker: {state['status']}", screen)
                self.assertIn(f"pid: {probe['worker_pid']}", screen)
                if probe.get("error"):
                    self.assertIn(probe["error"], screen)

    def test_physical_terminal_shows_the_probe_status_and_publication_health(
        self,
    ) -> None:
        for health in ("healthy", "warming", "stale", "error"):
            with (
                self.subTest(health=health),
                patch.object(
                    streaming,
                    "fetch_observation_publication",
                    return_value=_publication(health=health),
                ),
                patch.object(streaming, "RuntimeViewServer"),
                patch.object(streaming, "physical_observation_dir"),
                patch.object(streaming, "_publish_physical_view"),
            ):
                machine, json_text = self._stream(PICAR, json_output=True)
                terminal, screen = self._stream(PICAR, no_clear=True)
                probe = json.loads(json_text)
                self.assertEqual(terminal.exit_code, machine.exit_code)
                self.assertIn(f"status: {probe['status']}", screen)
                self.assertIn(f"publication: {health}", screen)
                if probe.get("error"):
                    self.assertIn(probe["error"], screen)

    def test_cli_stream_perception_help_lists_json(self) -> None:
        result = run_automa("vehicles", "stream", "perception", "--help", check=False)
        self.assertEqual(result.returncode, 0)
        help_text = " ".join(result.stdout.split())
        self.assertIn("--json", help_text)
        self.assertIn("exit 2 unless perception is live", help_text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
