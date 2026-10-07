from __future__ import annotations

import unittest

from autonomy.runtime.cycle_host import AutonomyCycleHost
from autonomy.runtime.execution import ControlExecution
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
