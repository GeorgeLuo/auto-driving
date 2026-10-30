from __future__ import annotations

import contextlib
import json
import os
import re
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from cli.automa_cli.simulators import store as simulator_store
from cli.automa_cli.simulators.service import create_simulator
from cli.automa_cli.simulators.render import render_simulator_result
from tests.shared.cli.invoke import invoke_automa
from tests.support.fake_headless_sim import fake_headless_sim_command
from tests.support.fake_metrics_ui import fake_metrics_ui
from tests.support.runtime_fixtures import write_host_fixture


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def _launcher(source: str) -> str:
    return shlex.join([sys.executable, "-c", source])


class CreatedSimulatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime_root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.ui_dir = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for part in ("scripts/play-chase-headless.mjs", "node_modules/.bin/tsx"):
            (self.ui_dir / part).parent.mkdir(parents=True, exist_ok=True)
            (self.ui_dir / part).touch()
        self.default = self.enterContext(fake_metrics_ui())
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}/"
        # The default simulator answers on the first port, so create skips it.
        self.enterContext(patch("cli.automa_cli.simulators.service.SIMULATOR_PORTS",
                                [self.default.server_address[1], self.port]))
        self.addCleanup(self.end_simulators)

    def env(self, launcher: str | None = None) -> dict[str, str]:
        return {
            "CHASE_UI_WS_URL": self.default.url,
            "AUTOMA_METRICS_UI_DIR": str(self.ui_dir),
            "AUTOMA_HEADLESS_SIM_COMMAND": launcher or fake_headless_sim_command(),
        }

    def automa(self, *args: str, launcher: str | None = None) -> subprocess.CompletedProcess[str]:
        return invoke_automa(*args, runtime_root=self.runtime_root, extra_env=self.env(launcher), check=False)

    def records(self) -> list[dict[str, Any]]:
        return [json.loads(path.read_text(encoding="utf-8"))
                for path in sorted((self.runtime_root / "simulators").glob("*/simulator.json"))]

    def end_simulators(self) -> None:
        for record in self.records():
            with contextlib.suppress(ProcessLookupError):
                os.killpg(record["pid"], signal.SIGKILL)
            with contextlib.suppress(ChildProcessError):
                os.waitpid(record["pid"], 0)

    def status_blocks(self) -> list[list[str]]:
        return [block.splitlines() for block in self.automa("vehicles", "status").stdout.strip().split("\n\n")]

    def test_a_created_simulator_is_listed_in_status_until_it_is_deleted(self) -> None:
        created = self.automa("simulators", "create")

        self.assertEqual((created.returncode, created.stdout), (0, f"Created Simulator (chase-sim) at {self.url}\n"))
        [record] = self.records()
        self.assertEqual((record["port"], record["url"], record["config"]),
                         (self.port, f"ws://127.0.0.1:{self.port}/ws/control", "default"))
        self.assertEqual(self.status_blocks()[1], [
            f"Simulator (chase-sim)  running at {self.url}",
            "  frontend connected, game chase",
            f"  created by simulators create, pid {record['pid']}",
        ])
        vehicles = json.loads(self.automa("vehicles", "status", "--json").stdout)["vehicles"]
        self.assertEqual([vehicle["pid"] for vehicle in vehicles], [None, record["pid"]])

        deleted = self.automa("simulators", "delete", "--url", self.url)

        self.assertEqual((deleted.returncode, deleted.stdout), (0, f"Deleted Simulator (chase-sim) at {self.url}\n"))
        self.assertTrue(_gone(record["pid"]))
        self.assertEqual(self.records(), [])
        self.assertEqual(len(self.status_blocks()), 1)

    def test_a_launcher_that_exits_before_it_is_ready_records_nothing(self) -> None:
        result = self.automa("simulators", "create", launcher=_launcher(
            "import sys; print('Chrome not found; pass --chrome or set CHROME_PATH'); sys.exit(1)"
        ))

        self.assertEqual(result.returncode, 2)
        message = result.stdout.splitlines()
        self.assertEqual(message[0], "The simulator exited with code 1 before it was ready.")
        self.assertTrue(message[1].startswith("Simulator log "), message)
        self.assertEqual(message[2:], ["Chrome not found; pass --chrome or set CHROME_PATH"])
        self.assertEqual(self.records(), [])

    def test_a_launcher_that_is_not_ready_in_time_is_ended(self) -> None:
        launcher = _launcher("import os, time; print('pid', os.getpid(), flush=True); time.sleep(60)")
        with patch.dict(os.environ, self.env(launcher)), patch.object(simulator_store, "RUNTIME_ROOT", self.runtime_root):
            result = create_simulator(timeout_s=0.5)

        self.assertEqual(result.exit_code, 2)
        self.assertTrue(render_simulator_result(result).startswith("The simulator was not ready within 0.5 s.\n"), render_simulator_result(result))
        pid = int(re.search(r"^pid (\d+)$", render_simulator_result(result), re.MULTILINE).group(1))
        self.assertTrue(_gone(pid))
        self.assertEqual(self.records(), [])

    def test_a_simulator_whose_process_ended_drops_out_of_status(self) -> None:
        self.automa("simulators", "create")
        [record] = self.records()
        os.killpg(record["pid"], signal.SIGKILL)
        os.waitpid(record["pid"], 0)

        self.assertEqual(len(self.status_blocks()), 1)
        deleted = self.automa("simulators", "delete", "--url", self.url)
        self.assertEqual((deleted.returncode, deleted.stdout),
                         (2, f"No created simulator at {self.url}. None created.\n"))

    def test_delete_refuses_while_a_host_was_created_at_the_simulator(self) -> None:
        self.automa("simulators", "create")
        write_host_fixture(self.runtime_root, "demo", url=f"ws://127.0.0.1:{self.port}/ws/control")

        deleted = self.automa("simulators", "delete", "--url", self.url)

        self.assertEqual((deleted.returncode, deleted.stdout),
                         (2, f"Host demo uses the simulator at {self.url}; delete it first.\n"))
        [record] = self.records()
        self.assertFalse(_gone(record["pid"]))

    def test_create_needs_an_installed_metrics_ui_checkout(self) -> None:
        unset = invoke_automa("simulators", "create", runtime_root=self.runtime_root,
                              extra_env={**self.env(), "AUTOMA_METRICS_UI_DIR": ""}, check=False)
        (self.ui_dir / "node_modules/.bin/tsx").unlink()
        uninstalled = self.automa("simulators", "create")

        self.assertEqual((unset.returncode, unset.stdout), (2, (
            "Cannot create a simulator: AUTOMA_METRICS_UI_DIR, the Metrics UI checkout to run it from, is not set.\n"
        )))
        self.assertEqual((uninstalled.returncode, uninstalled.stdout), (2, (
            f"Cannot create a simulator: {self.ui_dir} has no node_modules/.bin/tsx; its packages are not installed.\n"
        )))
        self.assertEqual(self.records(), [])


if __name__ == "__main__":
    unittest.main()
