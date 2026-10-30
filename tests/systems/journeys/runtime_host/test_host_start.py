"""Starting a Chase host from the CLI: a start that does not finish leaves no host running."""
from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from tests.support.runtime_fixtures import stage_step, write_host_fixture
from typing import Iterator
from unittest import mock

from cli.automa_cli.hosts import local, staging
from cli.automa_cli.hosts.local import ensure_chase_host
from cli.automa_cli.releases.errors import RuntimeHostError
from tests.shared.implementations.runtime.wait import wait_until
from tests.support.fake_metrics_ui import fake_metrics_ui_server

HOST_NAME = "chase-sim-chaser"
POPEN = subprocess.Popen


@contextmanager
def _staged_vehicle() -> Iterator[list[subprocess.Popen]]:
    """A staged Chase vehicle on a fake simulator, and each supervisor a host start launches."""

    launched: list[subprocess.Popen] = []

    def launch(*args, **kwargs):
        launched.append(POPEN(*args, **kwargs))
        return launched[-1]

    with tempfile.TemporaryDirectory() as tmp, fake_metrics_ui_server() as ws_url:
        root = Path(tmp) / "vehicles"
        env = {"CHASE_UI_WS_URL": ws_url}
        write_host_fixture(root, "chase-sim-chaser", url=ws_url)
        stage_step(root, HOST_NAME, "perception")
        try:
            with (
                mock.patch.dict(os.environ, {"AUTOMA_RUNTIME_ROOT": str(root), **env}),
                mock.patch.object(staging, "RUNTIME_ROOT", root),
                mock.patch("cli.automa_cli.releases.paths.RUNTIME_ROOT", root),
                mock.patch.object(local.subprocess, "Popen", side_effect=launch),
            ):
                yield launched
        finally:
            for process in launched:
                if not _session_ended(process.pid):
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait()


def _session_ended(session_id: int) -> bool:
    try:
        os.killpg(session_id, 0)
    except ProcessLookupError:
        return True
    return False


class HostStartTests(unittest.TestCase):
    def test_a_host_that_does_not_serve_in_time_is_ended(self) -> None:
        with _staged_vehicle() as launched:
            with self.assertRaisesRegex(RuntimeHostError, "did not serve within 0.01 s"):
                ensure_chase_host(HOST_NAME, timeout_s=0.01)
            [supervisor] = launched
            wait_until(lambda: _session_ended(supervisor.pid), message="the failed start left its supervisor running")

    def test_an_interrupted_start_ends_the_host_it_started(self) -> None:
        host_sessions = []
        wait_for_host = local._wait_for_host

        def interrupted(*args):
            host = wait_for_host(*args)
            host_sessions.append(os.getpgid(host["pid"]))
            raise KeyboardInterrupt

        with _staged_vehicle() as launched, mock.patch.object(local, "_wait_for_host", side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                ensure_chase_host(HOST_NAME)
            [supervisor] = launched
            self.assertEqual(host_sessions, [supervisor.pid])
            wait_until(lambda: _session_ended(supervisor.pid), message="the interrupted start left its host running")


if __name__ == "__main__":
    unittest.main()
