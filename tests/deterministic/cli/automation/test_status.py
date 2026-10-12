from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.support.runtime_fixtures import write_runtime_fixture


VEHICLE_ID = "chase-sim-chaser"
DEAD_PID = 999_999_999


class AutomationStatusTests(unittest.TestCase):


    def test_view_warmup_polling_is_bounded_by_command_budget(self) -> None:
        """view_timeout_s is one wall-clock budget for probes and warm-up sleeps."""

        from cli.automa_cli.automation import _collect_automation_status

        warming = {
            "schema": "automa_perception_view_v1",
            "available": False,
            "status": "warming",
            "url": "http://127.0.0.1:8898/",
            "reason": "waiting for first correlated frame",
        }
        probe_calls: list[float] = []

        def _probe(_automation_dir, *, timeout_s, **_kwargs):
            probe_calls.append(float(timeout_s))
            time.sleep(min(0.02, max(0.0, float(timeout_s))))
            return dict(warming)

        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            write_runtime_fixture(runtime_root, VEHICLE_ID, pid=os.getpid())
            with mock.patch(
                "cli.automa_cli.runtime_hosts.RUNTIME_ROOT",
                runtime_root,
            ), mock.patch(
                "cli.automa_cli.automation.get_perception_view_status",
                side_effect=_probe,
            ):
                started = time.monotonic()
                statuses = _collect_automation_status(
                    vehicle_id=VEHICLE_ID,
                    view_timeout_s=0.05,
                )
                elapsed = time.monotonic() - started

        self.assertEqual(len(statuses), 1)
        self.assertLess(
            elapsed,
            0.35,
            f"view warm-up exceeded remaining budget: elapsed={elapsed:.3f}s "
            f"probes={probe_calls}",
        )
        self.assertTrue(probe_calls)
        self.assertTrue(all(timeout <= 0.05 + 1e-9 for timeout in probe_calls))
        self.assertFalse(statuses[0]["published_view"]["available"])

    def test_aggregate_view_probes_share_one_command_deadline(self) -> None:
        """Aggregate status must not restart view_timeout_s for each runtime."""

        from cli.automa_cli.automation import _collect_automation_status

        warming = {
            "schema": "automa_perception_view_v1",
            "available": False,
            "status": "warming",
            "url": "http://127.0.0.1:8898/",
            "reason": "waiting for first correlated frame",
        }
        probe_calls: list[float] = []

        def _probe(_automation_dir, *, timeout_s, **_kwargs):
            probe_calls.append(float(timeout_s))
            time.sleep(min(0.05, max(0.0, float(timeout_s))))
            return dict(warming)

        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            write_runtime_fixture(runtime_root, "chase-sim-chaser", pid=os.getpid())
            write_runtime_fixture(runtime_root, "chase-sim-runner", pid=os.getpid())
            with mock.patch(
                "cli.automa_cli.runtime_hosts.RUNTIME_ROOT",
                runtime_root,
            ), mock.patch(
                "cli.automa_cli.automation.get_perception_view_status",
                side_effect=_probe,
            ):
                started = time.monotonic()
                statuses = _collect_automation_status(
                    vehicle_id=None,
                    view_timeout_s=0.2,
                )
                elapsed = time.monotonic() - started

        self.assertEqual(len(statuses), 2)
        self.assertLess(
            elapsed,
            0.35,
            f"aggregate view probes exceeded one shared budget: elapsed={elapsed:.3f}s "
            f"probes={probe_calls}",
        )
        # Two independent 0.2s budgets would allow ~0.4s+; shared budget must not.
        self.assertLess(elapsed, 0.4)
        self.assertTrue(probe_calls)
        self.assertTrue(all(timeout <= 0.2 + 1e-9 for timeout in probe_calls))


if __name__ == "__main__":
    unittest.main(verbosity=2)
