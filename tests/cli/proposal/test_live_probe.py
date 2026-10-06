from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autonomy.decision_cycle.steps import step_runner
from cli.automa_cli.streaming import (
    PROPOSAL_LIVE_SCHEMA,
    format_live_step_screen,
    probe_live_step,
)
from implementations.decision_cycle.catalog import packaged_activation

CHASE = {"vehicle_id": "chase-sim-chaser", "provider": "chase-sim"}
PICAR = {
    "vehicle_id": "piracer",
    "provider": "picar",
    "connection": {"base_url": "http://piracer.local:8887"},
}
AUTOMATION_COMMAND = (
    "/usr/bin/python3 /repo/cli/automa vehicles automation run "
    "--id chase-sim-chaser --foreground --observe-only"
)


def _proposal_status() -> dict:
    """A proposal runner's status as the engine reports it."""

    return json.loads(json.dumps(step_runner(packaged_activation("proposal")).status()))


class ProposalLiveProbeTests(unittest.TestCase):
    def _probe_chase(self, proposal: dict) -> dict:
        now = 1_700_000_000_000
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            state = {"status": "running", "pid": 424242, "updated_at_ms": now - 500}
            state_path.write_text(json.dumps({**state, "proposal": proposal}), encoding="utf-8")
            with patch(
                "cli.automa_cli.streaming._automation_dir", return_value=state_path.parent
            ), patch("cli.automa_cli.automation._pid_alive", return_value=True), patch(
                "cli.automa_cli.automation._process_command", return_value=AUTOMATION_COMMAND
            ), patch("cli.automa_cli.streaming.time.time", return_value=now / 1000.0):
                return probe_live_step("proposal", vehicle_id="chase-sim-chaser", vehicle=CHASE)

    def test_chase_worker_reports_the_proposal_runner_it_runs(self) -> None:
        live = self._probe_chase({"activation": "proposal/active.json", "status": _proposal_status()})
        self.assertEqual(live["schema"], PROPOSAL_LIVE_SCHEMA)
        self.assertEqual(live["status"], "live")
        self.assertEqual(live["plugin_ids"], ["avoid_recent_obstruction"])
        self.assertEqual(live["selected_plugin_ids"], ["avoid_recent_obstruction"])
        self.assertEqual(live["run_count"], 0)
        self.assertEqual(live["activation"], "proposal/active.json")
        self.assertNotIn("update_count", live)
        screen = format_live_step_screen("proposal", vehicle_id="chase-sim-chaser", live=live)
        self.assertIn("Live proposal: chase-sim-chaser [live]", screen)
        self.assertIn("Applied plugins: avoid_recent_obstruction", screen)
        self.assertIn("Counters: runs=0 failures=0", screen)

    def test_chase_worker_without_proposals_names_the_restage_and_restart(self) -> None:
        live = self._probe_chase({"activation": "proposal/active.json", "status": "absent"})
        self.assertEqual(live["status"], "absent")
        self.assertIn("vehicles update proposal --id chase-sim-chaser", live["error"])

    def test_picar_reports_its_autonomy_status_proposal_step(self) -> None:
        status = {"drive_mode": "user", "autonomy": {"steps": {"proposal": _proposal_status()}}}
        with patch("cli.automa_cli.streaming.fetch_autonomy_status", return_value=status):
            live = probe_live_step("proposal", vehicle_id="piracer", vehicle=PICAR)
        self.assertEqual(live["status"], "live")
        self.assertEqual(live["endpoint"], "http://piracer.local:8887/autonomy/status")
        self.assertEqual(live["plugin_ids"], ["avoid_recent_obstruction"])
        self.assertNotIn("has_memory", live)

        with patch(
            "cli.automa_cli.streaming.fetch_autonomy_status",
            return_value={"autonomy": {"steps": {"proposal": None}}},
        ):
            absent = probe_live_step("proposal", vehicle_id="piracer", vehicle=PICAR)
        self.assertEqual(absent["status"], "absent")
        self.assertIn("No live proposal step", absent["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
