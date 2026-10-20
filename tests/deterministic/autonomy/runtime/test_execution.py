from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.runtime.control import AutonomyControl

from autonomy.runtime.cycle_host import AutonomyCycleHost
from autonomy.runtime.execution import CONTROL_HISTORY_LIMIT, ControlExecution
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
    def test_history_records_applied_commands_and_mode_change_stops(self) -> None:
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
        history = execution.recent_deliveries()
        self.assertEqual([item["reason"] for item in history],
                         ["awaiting-fresh-decision", "turn", "mode-changed"])
        self.assertEqual(history[1], {
            "delivered_at_ms": application.delivered_at_ms, "steering": -0.5,
            "throttle": 0.3, "reason": "turn", "frame_id": "frame-1",
        })
        self.assertEqual((history[2]["steering"], history[2]["throttle"]), (0.0, 0.0))
        history[0]["reason"] = "tampered"
        self.assertEqual(execution.recent_deliveries()[0]["reason"], "awaiting-fresh-decision")

    def test_watchdog_stop_is_recorded(self) -> None:
        execution = ControlExecution(_RecordingTarget())
        execution._deadline = 0.0
        with patch.object(execution._wake, "wait", side_effect=[False, True]):
            execution._watch()
        self.assertEqual(execution.recent_deliveries()[0]["reason"], "decision-expired")
        self.assertEqual(execution.recent_deliveries()[0]["throttle"], 0.0)

    def test_history_is_bounded_and_undelivered_commands_are_absent(self) -> None:
        execution = ControlExecution(_RecordingTarget())
        self.addCleanup(execution.close)
        for index in range(CONTROL_HISTORY_LIMIT + 5):
            execution._write_idle(str(index))
        history = execution.recent_deliveries()
        self.assertEqual(len(history), CONTROL_HISTORY_LIMIT)
        self.assertEqual(history[0]["reason"], "5")
        failing = ControlExecution(_UnreachableTarget())
        with self.assertRaises(ConnectionError):
            failing.set_mode("autonomy")
        self.assertEqual(failing.recent_deliveries(), ())

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
