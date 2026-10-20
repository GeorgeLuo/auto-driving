from __future__ import annotations

import json
import unittest
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from unittest.mock import patch

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.runtime.control import AutonomyControl

from autonomy.runtime.cycle_host import AutonomyCycleHost
from autonomy.runtime.execution import CONTROL_APPLICATION_LIMIT, ControlApplication, ControlExecution
from autonomy.runtime.session import RunConfiguration


class _RecordingTarget:
    def __init__(self) -> None:
        self.writes: list[str] = []

    def acquire(self) -> None:
        pass

    def write(self, control) -> dict:
        self.writes.append(control.reason)
        return {"boundary": "test_target", "write": len(self.writes)}

    def release(self) -> None:
        pass


class _UnreachableTarget:
    def acquire(self) -> None:
        raise ConnectionError("unreachable")

    def write(self, control) -> dict:
        raise ConnectionError("unreachable")

    def release(self) -> None:
        pass


class ControlExecutionTests(unittest.TestCase):
    def test_applications_keep_control_identity_and_mode_boundaries(self) -> None:
        execution = ControlExecution(_RecordingTarget())
        self.addCleanup(execution.close)
        with patch("autonomy.runtime.execution.time.time", return_value=1.0):
            execution.set_mode("autonomy")
            result = SimpleNamespace(
                context=DecisionFrameContext("frame-1", 0, 1000),
                action=SimpleNamespace(status="ok"),
                control=AutonomyControl(steering=-0.5, throttle=0.3, reason="turn"),
            )
            application = execution.apply(result, execution.ticket())
            execution.set_mode("manual")
        history = execution.ticket().control_applications
        self.assertEqual([item.reason for item in history],
                         ["awaiting-fresh-decision", "turn", "mode-changed"])
        self.assertIs(history[1], application)
        self.assertEqual((history[0].mode, history[1].mode, history[2].mode),
                         ("autonomy", "autonomy", "manual"))
        self.assertEqual((history[2].control.steering, history[2].control.throttle), (0.0, 0.0))
        self.assertIs(history[0], execution.ticket().control_applications[0])
        with self.assertRaises(FrozenInstanceError):
            history[0].reason = "tampered"

    def test_manual_and_observe_only_applications_do_not_claim_operator_motion(self):
        from implementations.runtime.picar.control import DonkeyControlTarget
        for mode in ("manual", "observe_only"):
            with self.subTest(mode=mode):
                execution = ControlExecution(DonkeyControlTarget())
                self.addCleanup(execution.close)
                execution.set_mode("autonomy")
                execution.set_mode(mode)
                operator = AutonomyControl(steering=-1.0, throttle=0.8)
                self.assertIs(execution.output(operator), operator)
                ticket = execution.ticket()
                self.assertEqual(ticket.mode, mode)
                self.assertEqual(ticket.control_applications[-1].mode, mode)
                self.assertEqual(ticket.control_applications[-1].control.throttle, 0.0)
                self.assertIs(ticket.control_applications[-1], execution.ticket().control_applications[-1])

    def test_applications_round_trip_as_json_and_shared_memory_records(self):
        from autonomy.shared_memory import shared_memory_document, shared_memory_from_document
        application = ControlApplication(
            "frame", "autonomy", True, "drive", AutonomyControl(0.4, 0.3), 100,
            {"boundary": "donkey_runtime_output", "transport": "in_process"},
        )
        self.assertEqual(ControlApplication.from_dict(json.loads(json.dumps(application.to_dict()))), application)
        decoded = shared_memory_from_document(shared_memory_document({"applications": (application,)}))
        self.assertEqual(decoded["applications"], [application])

    def test_watchdog_stop_is_recorded(self) -> None:
        execution = ControlExecution(_RecordingTarget())
        execution._deadline = 0.0
        with patch.object(execution._wake, "wait", side_effect=[False, True]):
            execution._watch()
        self.assertEqual(execution.ticket().control_applications[0].reason, "decision-expired")
        self.assertEqual(execution.ticket().control_applications[0].control.throttle, 0.0)

    def test_applications_are_bounded_and_failed_writes_are_absent(self) -> None:
        execution = ControlExecution(_RecordingTarget())
        self.addCleanup(execution.close)
        for index in range(CONTROL_APPLICATION_LIMIT + 5):
            execution._write_idle(str(index))
        history = execution.ticket().control_applications
        self.assertEqual(len(history), CONTROL_APPLICATION_LIMIT)
        self.assertEqual(history[0].reason, "5")
        failing = ControlExecution(_UnreachableTarget())
        with self.assertRaises(ConnectionError):
            failing.set_mode("autonomy")
        self.assertEqual(failing.ticket().control_applications, ())

    def test_mode_changes_keep_the_idle_write_receipt(self) -> None:
        target = _RecordingTarget()
        execution = ControlExecution(target)

        taken = execution.set_mode("autonomy")["application"]
        released = execution.set_mode("manual")["application"]

        self.assertEqual(target.writes, ["awaiting-fresh-decision", "mode-changed"])
        self.assertEqual(
            (taken["mode"], taken["applied"], taken["receipt"]),
            ("autonomy", True, {"boundary": "test_target", "write": 1}),
        )
        self.assertEqual(
            (released["mode"], released["applied"], released["reason"], released["receipt"]),
            ("manual", True, "mode-changed", {"boundary": "test_target", "write": 2}),
        )

    def test_mode_change_without_a_write_records_the_mode(self) -> None:
        target = _RecordingTarget()
        application = ControlExecution(target).set_mode("observe_only")["application"]

        self.assertEqual(target.writes, [])
        self.assertEqual(
            (application["mode"], application["applied"], application["receipt"]),
            ("observe_only", False, None),
        )

    def test_close_after_a_failed_acquire_records_the_failed_stop(self) -> None:
        host = AutonomyCycleHost(target=_UnreachableTarget())
        with self.assertRaises(ConnectionError):
            host.start(RunConfiguration(mode="autonomy"))

        host.close()

        execution = host.session_status()["execution"]
        self.assertTrue(execution["closed"])
        self.assertEqual(execution["application"]["reason"], "delivery-error")
        self.assertIn("stop failed", execution["application"]["error"])


if __name__ == "__main__":
    unittest.main()
