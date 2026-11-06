"""A run worker started by a process that stays, as the viewer stays, is reaped when it exits."""

from __future__ import annotations

import sys
import unittest

from cli.automa_cli.hosts.runs.process import launch_worker, stop_worker
from cli.automa_cli.shared.io.processes import pid_alive
from tests.shared.implementations.runtime.wait import wait_until


class WorkerReapingTests(unittest.TestCase):
    def test_a_worker_that_exits_is_gone_while_its_parent_runs_on(self) -> None:
        worker = launch_worker([sys.executable, "-c", "pass"], log_path=None)

        self.assertTrue(wait_until(lambda: not pid_alive(worker.pid), timeout_s=10.0))

    def test_a_stopped_worker_is_seen_to_stop(self) -> None:
        # Unreaped, the worker that ended on SIGINT would stay a zombie, and the stop would time out.
        worker = launch_worker([sys.executable, "-c", "import time; time.sleep(60)"], log_path=None)
        self.addCleanup(lambda: pid_alive(worker.pid) and worker.kill())

        self.assertEqual(stop_worker(worker.pid, wait_s=5.0), "stopped")


if __name__ == "__main__":
    unittest.main()
