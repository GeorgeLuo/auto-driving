from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cli.automa_cli.providers.urls import chase_operator_url, normalize_chase_url
from cli.automa_cli.shared.io.processes import pid_alive
from cli.automa_cli.shared.paths import display_path
from cli.automa_cli.simulators.status import simulators_status
from cli.automa_cli.simulators.store import SIMULATOR_SCHEMA
from tests.deterministic.cli.simulators.test_store import _dead_pid
from tests.shared.cli.invoke import invoke_automa
from tests.support.fake_hosts import closed_url
from tests.support.fake_metrics_ui import fake_metrics_ui
from tests.support.runtime_fixtures import write_host_fixture


_JSON_FIELDS = {
    "url", "port", "pid", "state", "frontend", "game", "error", "hosts",
    "started_at_ms", "metrics_ui_dir", "config", "log", "log_tail",
}


class SimulatorsStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime_root = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def status(self, *args: str):
        return invoke_automa("simulators", "status", *args, runtime_root=self.runtime_root, check=False)

    def write_record(self, *, port: int, url: str, pid: int, log: str | None = None) -> Path:
        directory = self.runtime_root / "simulators" / str(port)
        directory.mkdir(parents=True)
        record = {
            "schema": SIMULATOR_SCHEMA,
            "pid": pid,
            "port": port,
            "url": url,
            "metrics_ui_dir": "/tmp/metrics-ui",
            "config": "default",
            "started_at_ms": 1793790000000,
        }
        (directory / "simulator.json").write_text(json.dumps(record), encoding="utf-8")
        log_path = directory / "simulator.log"
        if log is not None:
            log_path.write_text(log, encoding="utf-8")
        return log_path

    def test_none_created_is_an_empty_list(self) -> None:
        result = self.status()
        machine = self.status("--json")

        self.assertEqual((result.returncode, result.stdout), (0, "None created.\n"))
        self.assertEqual(json.loads(machine.stdout), {"schema": "automa_simulators_status", "simulators": []})
        self.assertEqual(machine.returncode, 0)

    def test_help_names_status(self) -> None:
        summary = invoke_automa("simulators", "help")
        parent = invoke_automa("simulators", "--help")
        details = invoke_automa("simulators", "status", "--help")

        self.assertIn("- status   show each simulator create started", summary.stdout)
        self.assertIn("Show each simulator simulators create started.", parent.stdout)
        self.assertIn(
            "Exits 1 when the --url simulator is missing, exited, or unreachable.",
            " ".join(details.stdout.split()),
        )
        self.assertIn("Create, show, and delete headless simulators.", invoke_automa("help").stdout)

    def test_a_running_simulator_shows_the_shared_block_and_its_host(self) -> None:
        server = self.enterContext(fake_metrics_ui())
        _host, port = server.server_address[:2]
        self.write_record(port=port, url=server.url, pid=os.getpid())
        write_host_fixture(self.runtime_root, "chaser", url=server.url)
        operator = chase_operator_url(server.url)

        result = self.status()
        named = self.status("--url", operator)

        block = (
            f"Simulator (chase-sim)  running at {operator}\n"
            "  frontend connected, game chase\n"
            f"  created by simulators create, pid {os.getpid()}\n"
            "  used by host chaser\n"
        )
        self.assertEqual((result.returncode, result.stdout), (0, block))
        self.assertEqual((named.returncode, named.stdout), (0, block))
        write_host_fixture(self.runtime_root, "follower", url=server.url)
        self.assertIn("used by hosts chaser, follower", self.status().stdout)

    def test_json_fields_do_not_change_with_long(self) -> None:
        server = self.enterContext(fake_metrics_ui())
        _host, port = server.server_address[:2]
        log_path = self.write_record(port=port, url=server.url, pid=os.getpid())

        short = json.loads(self.status("--json").stdout)
        long = json.loads(self.status("--json", "--long").stdout)
        text = self.status("--long")

        self.assertEqual(short, long)
        [simulator] = short["simulators"]
        self.assertEqual(set(simulator), _JSON_FIELDS)
        self.assertEqual(simulator["state"], "running")
        self.assertIsNone(simulator["log_tail"])
        self.assertEqual(simulator["log"], display_path(log_path))
        self.assertIn("Metrics UI checkout /tmp/metrics-ui, config default", text.stdout)
        self.assertIn(f"log {display_path(log_path)}", text.stdout)
        self.assertNotIn("log_tail", text.stdout)

    def test_an_exited_simulator_stays_in_the_list_and_fails_a_named_url(self) -> None:
        pid = _dead_pid()
        self.assertFalse(pid_alive(pid))
        url = "ws://127.0.0.1:5052/ws/control"
        self.write_record(port=5052, url=url, pid=pid, log="launcher said ready\n")
        operator = chase_operator_url(url)

        listed = self.status()
        named = self.status("--url", operator, "--json")
        payload = json.loads(named.stdout)

        self.assertEqual(listed.returncode, 0)
        self.assertIn(f"exited at {operator}", listed.stdout)
        self.assertIn("launcher said ready", listed.stdout)
        self.assertNotIn("frontend", listed.stdout)
        self.assertEqual(named.returncode, 1)
        [simulator] = payload["simulators"]
        self.assertEqual(simulator["state"], "exited")
        self.assertEqual(simulator["log_tail"], ["launcher said ready"])
        self.assertIsNone(simulator["frontend"])
        self.assertNotIn("log_text", simulator)

    def test_an_exited_simulator_without_a_log_has_no_tail(self) -> None:
        url = "ws://127.0.0.1:5053/ws/control"
        log_path = self.write_record(port=5053, url=url, pid=_dead_pid())

        listed = self.status()
        [simulator] = json.loads(self.status("--json").stdout)["simulators"]

        self.assertIn(f"  (no simulator log at {display_path(log_path)})", listed.stdout)
        self.assertEqual(simulator["state"], "exited")
        self.assertIsNone(simulator["log_tail"])

    def test_json_is_the_document_simulators_status_returns(self) -> None:
        self.write_record(port=5052, url="ws://127.0.0.1:5052/ws/control", pid=_dead_pid(), log="ready\n")
        returned = []

        def keep(**kwargs):
            returned.append(simulators_status(**kwargs))
            return returned[-1]

        with patch("cli.automa_cli.simulators.commands.simulators_status", keep):
            machine = json.loads(self.status("--json").stdout)

        [status] = returned
        self.assertEqual(status.exit_code, 0)
        self.assertEqual(machine, status.payload)
        self.assertEqual(status.created, ("http://127.0.0.1:5052/",))

    def test_an_unreachable_simulator_fails_only_when_named(self) -> None:
        closed = closed_url()
        url = normalize_chase_url(closed)
        port = int(url.split(":")[2].split("/")[0])
        self.write_record(port=port, url=url, pid=os.getpid())

        listed = self.status("--timeout-s", "0.5")
        named = self.status("--url", closed, "--timeout-s", "0.5")

        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertIn("unreachable", listed.stdout)
        self.assertEqual(named.returncode, 1)
        self.assertIn("unreachable", named.stdout)

    def test_a_missing_url_exits_1(self) -> None:
        self.write_record(port=5051, url="ws://127.0.0.1:5051/ws/control", pid=_dead_pid())

        missing = self.status("--url", "http://127.0.0.1:5099/")
        machine = self.status("--url", "http://127.0.0.1:5099/", "--json")

        self.assertEqual(missing.returncode, 1)
        self.assertEqual(
            missing.stdout,
            "No created simulator at http://127.0.0.1:5099/. Created: http://127.0.0.1:5051/.\n",
        )
        self.assertEqual(machine.returncode, 1)
        self.assertEqual(json.loads(machine.stdout)["simulators"], [])

    def test_a_bad_url_or_timeout_exits_2(self) -> None:
        bad_url = self.status("--url", "ftp://example.com")
        bad_timeout = self.status("--timeout-s", "0")

        self.assertEqual(bad_url.returncode, 2)
        self.assertIn("scheme", bad_url.stdout)
        self.assertEqual(bad_timeout.returncode, 2)
        self.assertIn("--timeout-s", bad_timeout.stderr)
        self.assertEqual(bad_timeout.stdout, "")
