from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cli.automa_cli.shared.io.processes import pid_alive
from cli.automa_cli.simulators import store as simulator_store
from cli.automa_cli.simulators.store import SIMULATOR_SCHEMA, created_simulators, simulator_records, write_simulator_record


def _dead_pid() -> int:
    """An id no process has, so the record is not a live simulator."""

    for pid in range(1_000_000_000, 1_000_000_020):
        if not pid_alive(pid):
            return pid
    raise AssertionError("no unused pid in the probe range")


class SimulatorRecordTests(unittest.TestCase):
    def test_a_dead_pid_stays_in_the_records_and_drops_out_of_the_live_list(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        live = {"schema": SIMULATOR_SCHEMA, "pid": os.getpid(), "port": 5051, "url": "ws://127.0.0.1:5051/ws/control"}
        dead = {"schema": SIMULATOR_SCHEMA, "pid": _dead_pid(), "port": 5052, "url": "ws://127.0.0.1:5052/ws/control"}
        with patch.object(simulator_store, "RUNTIME_ROOT", root):
            write_simulator_record(live)
            write_simulator_record(dead)
            (root / "simulators" / "5050").mkdir(parents=True)
            (root / "simulators" / "5050" / "simulator.json").write_text("{", encoding="utf-8")
            write_simulator_record({
                "schema": "something_else", "pid": os.getpid(), "port": 5049,
                "url": "ws://127.0.0.1:5049/ws/control",
            })

            self.assertEqual([record["port"] for record in simulator_records()], [5051, 5052])
            self.assertEqual(created_simulators(), [live])
