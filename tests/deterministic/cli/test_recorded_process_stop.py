"""Stopping a recorded Chase host or simulator checks the process command."""

from __future__ import annotations

import json
import os
import shlex
import signal
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cli.automa_cli.hosts.local import (
    HOST_RECORD,
    LOCAL_WORKER_MODULE,
    chase_host_command_matches,
    stop_chase_host,
    supervisor_command_matches,
)
from cli.automa_cli.hosts.local_worker import BOOT
from cli.automa_cli.hosts.staging import automation_dir
from cli.automa_cli.simulators.process import simulator_command_matches, stop_simulator
from cli.automa_cli.simulators.store import SIMULATOR_SCHEMA, simulator_dir, write_simulator_record
from implementations.runtime.chase_sim.service import HOST_RECORD_SCHEMA
from tests.support.fake_headless_sim import fake_headless_sim_command
from tests.support.runtime_fixtures import runtime_root_env

HOST = "car"
CHILD = 4242
SUPERVISOR = 4241
PORT = 5051
SIM_PID = 5151


def _no_sleep(_seconds: float) -> None:
    raise AssertionError("stop waited")


def _supervisor(host: str = HOST) -> str:
    # ps prints a resolved interpreter; the matcher does not compare it.
    return f"/resolved/Python -m {LOCAL_WORKER_MODULE} {host}"


def _child(host: str = HOST, *, equals: bool = False) -> str:
    flag = f"--host-name={host}" if equals else f"--host-name {host}"
    return f"/resolved/Python -I -c {BOOT} /release {flag} --port 9"


def _node(port: int = PORT, *, equals: bool = False) -> str:
    flag = f"--port={port}" if equals else f"--port {port}"
    return f"node /opt/ui/scripts/play-chase-headless.mjs {flag}"


class RecordedProcessStopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(runtime_root_env(self.root))
        self.enterContext(patch.dict(os.environ))
        os.environ.pop("AUTOMA_HEADLESS_SIM_COMMAND", None)
        self.signals: list[tuple[int, signal.Signals]] = []
        self.groups: list[tuple[int, signal.Signals]] = []

    def test_supervisor_command_is_the_local_worker_and_the_exact_host_name(self) -> None:
        self.assertTrue(supervisor_command_matches(_supervisor(), HOST))
        self.assertFalse(supervisor_command_matches(_supervisor("car-2"), HOST))
        self.assertFalse(supervisor_command_matches(_supervisor(""), HOST))
        self.assertFalse(supervisor_command_matches(f"/resolved/Python {LOCAL_WORKER_MODULE} {HOST}", HOST))
        self.assertFalse(supervisor_command_matches(f"/resolved/Python -m {LOCAL_WORKER_MODULE}", HOST))
        self.assertFalse(supervisor_command_matches(f"/resolved/Python -m {LOCAL_WORKER_MODULE} --name {HOST}", HOST))

    def test_chase_host_command_is_the_service_boot_and_the_exact_host_name(self) -> None:
        self.assertTrue(chase_host_command_matches(_child(), HOST))
        self.assertTrue(chase_host_command_matches(_child(equals=True), HOST))
        quoted = shlex.join([
            "/resolved/Python", "-I", "-c", BOOT, "/release", "--host-name", HOST,
        ])
        self.assertTrue(chase_host_command_matches(quoted, HOST))
        self.assertFalse(chase_host_command_matches(_child("car-2"), HOST))
        self.assertFalse(chase_host_command_matches(f"/resolved/Python -I -c {BOOT} /release", HOST))
        self.assertFalse(chase_host_command_matches(f"/resolved/Python -c 'print(1)' --host-name {HOST}", HOST))
        self.assertFalse(chase_host_command_matches("python -c 'unterminated", HOST))

    def test_simulator_command_is_the_launcher_on_its_port(self) -> None:
        self.assertTrue(simulator_command_matches(_node(), PORT))
        self.assertTrue(simulator_command_matches(_node(equals=True), PORT))
        self.assertTrue(simulator_command_matches("node scripts/play-chase-headless.mjs --port 5051", PORT))
        self.assertFalse(simulator_command_matches(_node(5052), PORT))
        self.assertFalse(simulator_command_matches(_node(50510), 5051))
        self.assertFalse(simulator_command_matches(f"{fake_headless_sim_command()} --port {PORT}", PORT))
        override = fake_headless_sim_command()
        launched = shlex.join([*shlex.split(override), "--port", str(PORT)])
        resolved = shlex.join([
            "/opt/homebrew/Python", *shlex.split(override)[1:], "--port", str(PORT),
        ])
        with patch.dict(os.environ, {"AUTOMA_HEADLESS_SIM_COMMAND": override}):
            self.assertTrue(simulator_command_matches(launched, PORT))
            self.assertTrue(simulator_command_matches(resolved, PORT))
            self.assertFalse(simulator_command_matches(_node(), PORT))
            self.assertFalse(simulator_command_matches(shlex.join([*shlex.split(override), "--port", "5052"]), PORT))
        self.assertFalse(simulator_command_matches("node --port '5051", PORT))

    def _write_host(self) -> Path:
        path = automation_dir(HOST) / HOST_RECORD
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "schema": HOST_RECORD_SCHEMA,
            "host_name": HOST,
            "pid": CHILD,
            "supervisor_pid": SUPERVISOR,
            "base_url": "http://127.0.0.1:9",
        }), encoding="utf-8")
        return path

    def _patch_host(
        self,
        commands: dict[int, str | None],
        alive: set[int],
        *,
        die_on_term: bool = True,
        fail: signal.Signals | None = None,
        replace_after_read: bool = False,
    ) -> None:
        seen: set[int] = set()

        def pid_alive(pid: int) -> bool:
            return pid in alive

        def command(pid: int) -> str | None:
            if replace_after_read and pid in seen:
                return "other-process"
            seen.add(pid)
            return commands.get(pid)

        def kill(pid: int, sig: signal.Signals) -> None:
            self.signals.append((pid, sig))
            if fail is not None and sig == fail:
                raise ProcessLookupError
            if die_on_term and sig == signal.SIGTERM:
                alive.discard(pid)

        self.enterContext(patch("cli.automa_cli.hosts.local.pid_alive", pid_alive))
        self.enterContext(patch("cli.automa_cli.hosts.local.process_command", command))
        self.enterContext(patch("cli.automa_cli.hosts.local.os.kill", kill))
        self.enterContext(patch("cli.automa_cli.hosts.local.time.sleep", _no_sleep))

    def test_stop_kills_a_matching_host_that_does_not_exit(self) -> None:
        path = self._write_host()
        self._patch_host({SUPERVISOR: _supervisor(), CHILD: _child()}, {SUPERVISOR, CHILD}, die_on_term=False)

        self.assertTrue(stop_chase_host(HOST, wait_s=0))

        self.assertEqual(self.signals, [
            (SUPERVISOR, signal.SIGTERM), (CHILD, signal.SIGTERM),
            (SUPERVISOR, signal.SIGKILL), (CHILD, signal.SIGKILL),
        ])
        self.assertFalse(path.exists())

    def test_stop_does_not_report_a_kill_when_sigterm_ends_the_host(self) -> None:
        path = self._write_host()
        self._patch_host({SUPERVISOR: _supervisor(), CHILD: _child()}, {SUPERVISOR, CHILD})

        self.assertFalse(stop_chase_host(HOST, wait_s=0))

        self.assertEqual(self.signals, [(SUPERVISOR, signal.SIGTERM), (CHILD, signal.SIGTERM)])
        self.assertFalse(path.exists())

    def test_stop_leaves_a_reused_pid_and_removes_the_record(self) -> None:
        path = self._write_host()
        self._patch_host({SUPERVISOR: "other-supervisor", CHILD: "other-child"}, {SUPERVISOR, CHILD})

        self.assertFalse(stop_chase_host(HOST, wait_s=0))

        self.assertEqual(self.signals, [])
        self.assertFalse(path.exists())

    def test_stop_signals_only_the_pid_whose_command_matches(self) -> None:
        self._write_host()
        self._patch_host({SUPERVISOR: _supervisor(), CHILD: "other-child"}, {SUPERVISOR, CHILD})

        self.assertFalse(stop_chase_host(HOST, wait_s=0))

        self.assertEqual(self.signals, [(SUPERVISOR, signal.SIGTERM)])

    def test_stop_signals_when_the_command_cannot_be_read(self) -> None:
        self._write_host()
        self._patch_host({SUPERVISOR: None, CHILD: None}, {SUPERVISOR, CHILD}, die_on_term=False)

        self.assertTrue(stop_chase_host(HOST, wait_s=0))

        self.assertEqual([pid for pid, _sig in self.signals], [SUPERVISOR, CHILD, SUPERVISOR, CHILD])

    def test_stop_does_not_kill_a_pid_reused_during_the_wait(self) -> None:
        path = self._write_host()
        self._patch_host(
            {SUPERVISOR: _supervisor(), CHILD: _child()}, {SUPERVISOR, CHILD},
            die_on_term=False, replace_after_read=True,
        )

        self.assertFalse(stop_chase_host(HOST, wait_s=0))

        self.assertEqual(self.signals, [(SUPERVISOR, signal.SIGTERM), (CHILD, signal.SIGTERM)])
        self.assertFalse(path.exists())

    def test_stop_treats_process_lookup_error_as_already_stopped(self) -> None:
        path = self._write_host()
        self._patch_host(
            {SUPERVISOR: _supervisor(), CHILD: _child()}, {SUPERVISOR, CHILD},
            die_on_term=False, fail=signal.SIGKILL,
        )

        self.assertFalse(stop_chase_host(HOST, wait_s=0))

        self.assertEqual(self.signals[-2:], [(SUPERVISOR, signal.SIGKILL), (CHILD, signal.SIGKILL)])
        self.assertFalse(path.exists())

    def test_stop_leaves_a_record_whose_pid_is_already_dead(self) -> None:
        path = self._write_host()
        self._patch_host({}, set())

        self.assertFalse(stop_chase_host(HOST, wait_s=0))

        self.assertEqual(self.signals, [])
        self.assertTrue(path.exists())

    def _write_simulator(self) -> tuple[dict[str, object], Path]:
        record = {
            "schema": SIMULATOR_SCHEMA,
            "pid": SIM_PID,
            "port": PORT,
            "url": f"ws://127.0.0.1:{PORT}/ws/control",
        }
        write_simulator_record(record)
        return record, simulator_dir(PORT) / "simulator.json"

    def _patch_simulator(self, command: str | None, *, kill_error: bool = False) -> None:
        answers = iter((True, False))

        def pid_alive(_pid: int) -> bool:
            return next(answers, False)

        def process_command(_pid: int) -> str | None:
            return command

        def killpg(pid: int, sig: signal.Signals) -> None:
            self.groups.append((pid, sig))
            if kill_error:
                raise ProcessLookupError

        self.enterContext(patch("cli.automa_cli.simulators.process.pid_alive", pid_alive))
        self.enterContext(patch("cli.automa_cli.simulators.process.process_command", process_command))
        self.enterContext(patch("cli.automa_cli.simulators.process.os.killpg", killpg))
        self.enterContext(patch("cli.automa_cli.simulators.process.time.sleep", _no_sleep))

    def test_stop_simulator_ends_a_matching_launcher(self) -> None:
        record, path = self._write_simulator()
        self._patch_simulator(_node())

        self.assertEqual(stop_simulator(record), "stopped")

        self.assertEqual(self.groups, [(SIM_PID, signal.SIGTERM)])
        self.assertFalse(path.exists())

    def test_stop_simulator_does_not_signal_a_reused_pid(self) -> None:
        record, path = self._write_simulator()
        self._patch_simulator(f"/resolved/Python other.py --port {PORT}")

        self.assertEqual(stop_simulator(record), "exited")

        self.assertEqual(self.groups, [])
        self.assertFalse(path.exists())

    def test_stop_simulator_signals_when_the_command_cannot_be_read(self) -> None:
        record, _path = self._write_simulator()
        self._patch_simulator(None)

        self.assertEqual(stop_simulator(record), "stopped")

        self.assertEqual(self.groups, [(SIM_PID, signal.SIGTERM)])

    def test_stop_simulator_accepts_the_fake_launcher_override(self) -> None:
        record, path = self._write_simulator()
        override = fake_headless_sim_command()
        script = shlex.split(override)[1]
        self._patch_simulator(f"/opt/homebrew/Python {script} --port {PORT}")
        self.enterContext(patch.dict(os.environ, {"AUTOMA_HEADLESS_SIM_COMMAND": override}))

        self.assertEqual(stop_simulator(record), "stopped")

        self.assertEqual(self.groups, [(SIM_PID, signal.SIGTERM)])
        self.assertFalse(path.exists())

    def test_stop_simulator_treats_process_lookup_error_as_already_stopped(self) -> None:
        record, path = self._write_simulator()
        self._patch_simulator(_node(), kill_error=True)

        self.assertEqual(stop_simulator(record), "stopped")

        self.assertEqual(self.groups, [(SIM_PID, signal.SIGTERM)])
        self.assertFalse(path.exists())
