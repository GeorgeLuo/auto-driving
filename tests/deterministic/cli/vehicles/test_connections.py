from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

from PIL import Image
from cli.automa_cli.connection_targets import ConnectionTarget, decode_target, resolve_target
from cli.automa_cli.connection_status import Deadline, assess_connection, verdict
from cli.automa_cli.connections import connect, connection_lock
from cli.automa_cli.connection_recovery import RecoveryError, managed_ui, recover_simulator
from cli.automa_cli.simulators import SimevalRun
from tests.shared.cli.invoke import invoke_automa

TARGET = ConnectionTarget("chase-sim-chaser", "chase-sim", "ws://localhost:6060/custom")
ENV = {"state": "ready", "vehicle": {"vehicle_id": TARGET.vehicle_id}, "capture": {}}


def runtime(mode="observe_only", state="running", identity="host-1"):
    return {"ok": True, "host_run_id": identity,
            "session": {"status": state, "configuration": {"mode": mode}}}


def sample(health="healthy", age=10):
    data = io.BytesIO()
    Image.new("RGB", (2, 2)).save(data, format="JPEG")
    return {"health": health, "result_age_ms": age, "perception": {},
            "frame": {"frame_id": "frame-1", "has_image": True}}, data.getvalue()


class ConnectionStatusTests(unittest.TestCase):
    def setUp(self):
        self.stack = __import__('contextlib').ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch("cli.automa_cli.connection_status.runtime_hosts.staged_connection", return_value={}))
        self.stack.enter_context(patch("cli.automa_cli.connection_status.runtime_hosts.running_base_url", return_value="http://host:8887"))
        self.client = self.stack.enter_context(patch("cli.automa_cli.connection_status.RuntimeClient")).return_value
        self.client.status.return_value = runtime()
        self.fetch = self.stack.enter_context(patch("cli.automa_cli.connection_status.fetch_observation_sample", return_value=sample()))

    def assess(self, **kw):
        return assess_connection(TARGET, Deadline(3), environment=kw.get("environment", ENV))

    def test_fresh_observation_connects_without_any_local_view_or_monitor(self):
        status = self.assess()
        self.assertEqual(status["state"], "connected")
        self.assertEqual(status["layers"]["presentation"]["state"], "optional")
        self.client.start.assert_not_called()

    def test_stale_unknown_and_invalid_observations_never_report_connected(self):
        for health, age, code in [("healthy", 9000, "observation_stale"),
                                 ("healthy", None, "observation_stale"),
                                 ("warming", 1, "observation_warming"),
                                 ("error", 1, "observation_error")]:
            with self.subTest(health=health, age=age):
                self.fetch.return_value = sample(health, age)
                self.assertEqual(self.assess()["reason_code"], code)
        self.fetch.return_value = (sample()[0], b"bad jpeg")
        self.assertEqual(self.assess()["reason_code"], "observation_invalid")

    def test_frontend_capture_and_wrong_game_remain_distinct(self):
        for code, state in [("frontend_disconnected", "repair_required"), ("wrong_game", "reset_required"),
                            ("capture_image_invalid", "blocked"), ("simulator_capability_missing", "blocked")]:
            with self.subTest(code=code):
                status = self.assess(environment={"state": "failed", "reason_code": code, "message": "original detail"})
                self.assertEqual(status["state"], state)
                self.assertEqual(status["reason_code"], code)
                self.assertEqual(status["message"], "original detail")
                for action in status["actions"]:
                    self.assertIn(TARGET.endpoint, action["argv"])

    def test_active_driving_blocks_even_when_frontend_is_down(self):
        self.client.status.return_value = runtime("autonomy")
        status = self.assess(environment={"state": "failed", "reason_code": "frontend_disconnected"})
        self.assertEqual(status["reason_code"], "active_session_conflict")
        self.assertEqual(status["actions"], [])

    def test_failed_session_exposes_its_error_instead_of_looking_stopped(self):
        failed = runtime(state="error")
        failed["session"]["error"] = "Camera stream subscription was rejected: frontend not connected"
        self.client.status.return_value = failed
        status = self.assess()
        self.assertEqual(status["state"], "repair_required")
        self.assertEqual(status["reason_code"], "session_failed")
        self.assertIn("subscription was rejected", status["message"])

    def test_host_restart_between_reads_does_not_report_connected(self):
        self.client.status.side_effect = [runtime(), runtime(identity="host-2")]
        self.assertEqual(self.assess()["reason_code"], "host_changed")

    def test_expired_deadline_is_unknown_and_performs_no_host_check(self):
        deadline = Deadline(1)
        deadline.end = 0
        status = assess_connection(TARGET, deadline, environment=ENV)
        self.assertEqual(status["state"], "unknown")
        self.client.status.assert_not_called()

    def test_target_mismatch_is_not_silently_repaired(self):
        with patch("cli.automa_cli.connection_status.runtime_hosts.staged_connection", return_value={"ws_url": "ws://other:5050/ws/control"}):
            status = self.assess()
        self.assertEqual(status["reason_code"], "target_mismatch")
        self.assertIn("http://other:5050", status["details"]["command"])

    def test_loopback_aliases_of_the_staged_endpoint_match(self):
        with patch("cli.automa_cli.connection_status.runtime_hosts.staged_connection",
                   return_value={"ws_url": "ws://127.0.0.1:6060/custom"}):
            self.assertEqual(self.assess()["reason_code"], "connected")

    def test_picar_uses_the_same_host_observation_gate(self):
        target = ConnectionTarget("picar-1", "picar", "http://pi:8887")
        status = assess_connection(target, Deadline(3))
        self.assertEqual(status["state"], "connected")
        self.assertEqual(status["layers"]["environment"]["state"], "not_applicable")
        with self.assertRaisesRegex(RecoveryError, "does not apply"):
            recover_simulator(target, reset=True, reason="unavailable", deadline=Deadline(1), emit=Mock())


class ConnectionJourneyTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        p = patch("cli.automa_cli.runtime_hosts.RUNTIME_ROOT", self.root)
        p.start()
        self.addCleanup(p.stop)

    def run_connect(self, states, **kwargs):
        output = io.StringIO()
        with patch("cli.automa_cli.connections.assess_connection", side_effect=states):
            code, result = connect(TARGET, events=True, output=output, **kwargs)
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([e["sequence"] for e in events], list(range(1, len(events) + 1)))
        self.assertEqual(sum(e["event"] == "result" for e in events), 1)
        self.assertEqual(events[-1]["exit_code"], code)
        return result, events

    def test_connect_stops_at_repair_and_suggested_command_keeps_endpoint(self):
        status = verdict(TARGET, "repair_required", "frontend_disconnected", "Frontend gone", action="repair")
        with patch("cli.automa_cli.connections.recover_simulator") as repair:
            result, events = self.run_connect([status])
        repair.assert_not_called()
        argv = result["actions"][0]["argv"]
        self.assertIn(TARGET.endpoint, argv)
        self.assertIn("--allow-repair", argv)
        self.assertEqual(events[-2]["event"], "escalation_required")

    def test_following_repair_command_connects_and_streams_progress(self):
        missing = verdict(TARGET, "repair_required", "preparation_missing", "No release", action="repair", layers={"environment": ENV})
        ready = verdict(TARGET, "ready", "host_stopped", "Start", layers={"host": {"state": "stopped"}})
        healthy = verdict(TARGET, "connected", "connected", "Fresh")
        with patch("cli.automa_cli.connections.prepare_runtime") as prepare, patch("cli.automa_cli.connections._attach") as attach:
            result, _ = self.run_connect([missing, ready, healthy], allow_repair=True)
        prepare.assert_called_once()
        attach.assert_called_once()
        self.assertEqual(result["state"], "connected")
        self.assertEqual(result["completed_effects"], ["minor_repair", "observation_started_or_reused"])

    def test_repeated_connect_reuses_healthy_host(self):
        healthy = verdict(TARGET, "connected", "connected", "Fresh")
        with patch("cli.automa_cli.connections._attach") as attach:
            for _ in range(2):
                self.run_connect([healthy])
        attach.assert_not_called()

    def test_reset_requires_its_own_authorization(self):
        status = verdict(TARGET, "reset_required", "wrong_game", "Wrong game", action="reset")
        with patch("cli.automa_cli.connections.recover_simulator") as recovery:
            result, _ = self.run_connect([status], allow_repair=True)
        recovery.assert_not_called()
        self.assertEqual(result["state"], "reset_required")

    def test_repair_failure_preserves_original_error_and_does_not_bounce(self):
        status = verdict(TARGET, "repair_required", "preparation_missing", "Original failure", layers={"environment": ENV})
        with patch("cli.automa_cli.connections.prepare_runtime", side_effect=RecoveryError("preparation_failed", "New failure")):
            result, _ = self.run_connect([status], allow_repair=True)
        self.assertEqual(result["history"][0]["message"], "Original failure")
        self.assertEqual(result["reason_code"], "preparation_failed")

    def test_cancel_returns_terminal_event(self):
        result, events = self.run_connect([KeyboardInterrupt()])
        self.assertEqual(result["state"], "cancelled")
        self.assertEqual(events[-1]["event"], "result")

    def test_duplicate_operation_is_reported_without_starting_another_host(self):
        with connection_lock(TARGET):
            result, _ = self.run_connect([])
        self.assertEqual(result["reason_code"], "operation_in_progress")

    def test_invalid_target_and_timeout_return_parseable_machine_errors(self):
        for args in [("--target", "bad"), ("--timeout-s", "nan")]:
            result = invoke_automa("vehicles", "connect", *args, "--events", check=False)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout)["event"], "result")

    def test_reference_round_trip_and_ambiguous_default_selection(self):
        self.assertEqual(decode_target(TARGET.reference), TARGET)
        pi = ConnectionTarget("picar-1", "picar", "http://pi:8887")
        with patch("cli.automa_cli.connection_targets.configured_targets", return_value=[TARGET, pi]):
            with self.assertRaisesRegex(ValueError, "Ambiguous"):
                resolve_target()
        self.assertEqual(resolve_target(reference=TARGET.reference), TARGET)


class ManagedRecoveryTests(unittest.TestCase):
    def test_unrelated_running_deployment_cannot_be_reset(self):
        payload = {"ui": {"deployments": [{"url": "http://localhost:5050", "uiDir": "/somewhere", "mode": "dev"}]}}
        run = SimevalRun((), 0, json.dumps(payload), "", 1)
        with patch("cli.automa_cli.connection_recovery._simeval_executable", return_value="simeval"), patch(
            "cli.automa_cli.connection_recovery._run_process", return_value=run):
            with self.assertRaisesRegex(RecoveryError, "No unique"):
                managed_ui(TARGET, Deadline(3), Mock())
