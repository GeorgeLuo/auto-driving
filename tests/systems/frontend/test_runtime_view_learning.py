from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from autonomy.decision_cycle.memory.beliefs import Belief, Trial
from cli.automa_cli.learning_history import LearningHistory
from tests.support.cli_runner import WORKSPACE_ROOT as ROOT


def _status(t: int, beliefs: list[Belief], plugins: list[dict]) -> dict:
    return {
        "last_cycle": {"context": {"timestamp_ms": t}},
        "steps": {"memory": {
            "reset_count": 0,
            "beliefs": {belief.belief_id: belief.to_dict() for belief in beliefs},
            "plugins": plugins,
        }},
    }


def _plugin(phase: str, update_count: int, offset: float) -> dict:
    return {
        "plugin_id": "approach_model", "plugin_spec": "approach_model:Plugin", "activation": None,
        "update_count": update_count, "reset_count": 0, "failure_count": 0,
        "last_duration_ms": 0.4, "last_error": None,
        "state": {"phase": phase, "samples": update_count,
                  "rows": {str(row): 100.0 + 5 * row + offset for row in range(12)}},
    }


def _payloads() -> dict:
    """The /api/learning answers the page gets, built by the real history from real beliefs."""

    run = Belief("approach_model.run_ms", 1200.0, "approach_model", 1000,
                 properties={"p25": 1000.0, "p75": 1400.0, "samples": 4})
    run = run.opened(Trial("run_1", "run_duration", 1000, [1000.0, 1400.0]), 4)
    row = Belief("stop_row.row", 3.5, "stop_row", 1000).opened(Trial("stop_1", "stop", 1000, 3.5), 4)
    history = LearningHistory()
    history.ingest(_status(1000, [run, row], [_plugin("idle", 1, 0.0)]))
    first = history.payload(0)

    run = (run.resolved("run_1", "support", 1300.0, 2000, 64)
           .opened(Trial("run_2", "run_duration", 2000, [1000.0, 1400.0]), 4))
    row = row.withdrawn(reason="no_stop")
    history.ingest(_status(2000, [run, row], [_plugin("timing", 2, 3.0)]))
    run = (run.resolved("run_2", "contradict", 1600.0, 3000, 64)
           .revised(1300.0, 3000, properties={"p25": 1100.0, "p75": 1500.0, "samples": 6}))
    history.ingest(_status(3000, [run, row], [_plugin("idle", 3, 6.0)]))
    second = history.payload(first["seq"])

    restarted = LearningHistory()
    restarted.ingest(_status(500, [row], [_plugin("idle", 1, 0.0)]))
    return {
        "first": first,
        "second": second,
        "restarted_since": restarted.payload(second["seq"]),
        "restarted": restarted.payload(0),
    }


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class RuntimeViewLearningTests(unittest.TestCase):
    def test_learning_page_charts_every_belief_and_follows_the_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp) / "learning.json"
            fixture.write_text(json.dumps(_payloads()), encoding="utf-8")
            result = subprocess.run(
                ["node", str(Path(__file__).with_name("runtime_view_learning.js")), str(fixture)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("runtime view learning ok", result.stdout)
