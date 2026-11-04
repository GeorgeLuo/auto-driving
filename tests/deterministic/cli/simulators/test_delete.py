from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from cli.automa_cli.providers.urls import chase_operator_url
from cli.automa_cli.simulators.store import SIMULATOR_SCHEMA
from tests.deterministic.cli.simulators.test_store import _dead_pid
from tests.shared.cli.invoke import invoke_automa
from tests.support.runtime_fixtures import write_host_fixture


class DeleteExitedSimulatorTests(unittest.TestCase):
    """simulators delete on a record whose process has exited, which simulators status lists."""

    URL = "ws://127.0.0.1:5052/ws/control"

    def setUp(self) -> None:
        self.runtime_root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.record = self.runtime_root / "simulators" / "5052" / "simulator.json"
        self.record.parent.mkdir(parents=True)
        self.record.write_text(json.dumps({
            "schema": SIMULATOR_SCHEMA, "pid": _dead_pid(), "port": 5052, "url": self.URL,
            "metrics_ui_dir": "/tmp/metrics-ui", "config": "default", "started_at_ms": 1793790000000,
        }), encoding="utf-8")
        self.operator = chase_operator_url(self.URL)

    def automa(self, *args: str):
        return invoke_automa("simulators", *args, runtime_root=self.runtime_root, check=False)

    def test_delete_removes_the_record_of_an_exited_simulator(self) -> None:
        deleted = self.automa("delete", "--url", self.operator)

        self.assertEqual(
            (deleted.returncode, deleted.stdout),
            (0, f"Deleted Simulator (chase-sim) at {self.operator}, which had exited\n"),
        )
        self.assertFalse(self.record.exists())
        self.assertEqual(self.automa("status").stdout, "None created.\n")

    def test_a_missing_url_names_exited_records_too(self) -> None:
        missing = self.automa("delete", "--url", "http://127.0.0.1:5099/")

        self.assertEqual(
            (missing.returncode, missing.stdout),
            (2, f"No created simulator at http://127.0.0.1:5099/. Created: {self.operator}.\n"),
        )

    def test_a_host_at_an_exited_simulator_still_refuses(self) -> None:
        write_host_fixture(self.runtime_root, "chaser", url=self.URL)

        refused = self.automa("delete", "--url", self.operator)

        self.assertEqual(
            (refused.returncode, refused.stdout),
            (2, f"Host chaser uses the simulator at {self.operator}; delete it first.\n"),
        )
        self.assertTrue(self.record.exists())


if __name__ == "__main__":
    unittest.main()
