"""Offline proposal, plan, and action inspect over images and recorded runs."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autonomy.decision_cycle.activation import DECISION_STEPS, STEPS
from autonomy.decision_cycle.steps import CLEARABLE_STEPS
from cli.automa_cli.decision_steps import format_step_record, inspect_decision_step
from cli.automa_cli.step_replay import StepReplay, default_selection
from cli.automa_cli.workbench_source import ImageSource, ReplayFrame
from implementations.decision_cycle.catalog import SELECTABLE_STEPS
from implementations.decision_cycle.memory.plugins.bounded_evidence.plugin import LEDGER_KEY
from implementations.decision_cycle.memory.shared.evidence_ledger.ledger import (
    LedgerBounds,
    empty_ledger,
)
from tests.shared.cli.invoke import invoke_automa
from tests.shared.cli.memory.frame_fixtures import write_frames


def _source(metadata: dict | None = None) -> ImageSource:
    frame = ReplayFrame("automation", "frame-0", 0, 0, 0, None, metadata=metadata or {})
    return ImageSource(source_path=Path("automation"), source_id="automation", frames=(frame,))


class DecisionStepInspectTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.frames = write_frames(self.tmp / "frames")

    def inspect(self, step: str, source: Path | None = None, **options) -> dict:
        result = inspect_decision_step(step, source=source or self.frames, json_output=True, **options)
        self.assertEqual(result.exit_code, 0, result.message)
        return json.loads(result.message)

    def test_each_step_reports_its_record_for_every_frame(self) -> None:
        for step in DECISION_STEPS:
            with self.subTest(step=step):
                report = self.inspect(step)
                self.assertEqual(report["schema"], f"{step}_inspect_v1")
                self.assertEqual(report["source"]["frame_count"], 3)
                self.assertEqual([item["position"] for item in report["frames"]], [0, 1, 2])
                for item in report["frames"]:
                    self.assertIsNotNone(item["record"], item)
                    self.assertIn("status", item["record"])
                self.assertEqual(
                    set(report["selections"]),
                    {"perception", "observation", "memory", *DECISION_STEPS},
                )
                text = inspect_decision_step(step, source=self.frames).message
                self.assertIn(f"{step.capitalize()} inspect", text)
                self.assertIn(f"{step.capitalize()}: status=", text)

    def test_proposal_lines_show_observation_each_plugin_and_only_publisher_evidence(self) -> None:
        retained = [
            {"record_id": f"r-{index}", "kind": "obstacle", "confidence": None,
             "origin": {"frame_id": "frame-0"}}
            for index in range(13)
        ]
        memory = {
            "plugins": [
                {"plugin_id": "model", "state": {"health": "healthy", "records": [{"record_id": "wrong"}]}},
                {"plugin_id": "ledger", "state": {
                    "health": "healthy", "record_count": 13, "epoch_id": "epoch-1", "records": retained,
                }},
            ],
            "evidence_publisher": "ledger",
        }
        lines = format_step_record(
            "proposal", {"status": "ready"}, observation={"observation_id": "obs-1"}, memory=memory,
        )
        self.assertIn("Observation: obs-1", lines)
        self.assertIn("Memory: plugin=model health=healthy records=0 epoch=None", lines)
        self.assertIn("Memory: plugin=ledger health=healthy records=13 epoch=epoch-1", lines)
        self.assertIn("Memory: publisher=ledger", lines)
        self.assertEqual(len([line for line in lines if line.startswith("Retained:")]), 12)
        self.assertIn("Retained: record_id=r-0 kind=obstacle confidence=none frame_id=frame-0", lines)
        self.assertNotIn("wrong", "\n".join(lines))
        absent = format_step_record("proposal", {"status": "ready"})
        self.assertIn("Observation: none", absent)
        self.assertIn("Memory: publisher=none", absent)

    def test_proposal_inspect_carries_the_evidence_used_by_its_formatter(self) -> None:
        frame = self.inspect("proposal")["frames"][0]
        self.assertIsInstance(frame["observation"], dict)
        self.assertIsInstance(frame["memory"], dict)
        text = inspect_decision_step("proposal", source=self.frames).message
        self.assertIn(f"Observation: {frame['observation']['observation_id']}", text)
        self.assertIn("Memory: plugin=bounded_evidence", text)

    def test_each_frame_carries_every_steps_and_plugins_time(self) -> None:
        """Regression: no command showed step or plugin time, so a slow plugin was invisible."""

        report = self.inspect("plan")
        for item in report["frames"]:
            timing = item["timing"]
            self.assertEqual([entry["step"] for entry in timing["steps"]], list(STEPS))
            for entry in timing["steps"]:
                self.assertEqual(
                    [plugin["plugin_id"] for plugin in entry["plugins"]],
                    report["selections"][entry["step"]]["plugins"],
                )
                self.assertGreaterEqual(timing["duration_ms"], entry["duration_ms"])
                for plugin in entry["plugins"]:
                    self.assertGreaterEqual(entry["duration_ms"], plugin["duration_ms"])
        text = inspect_decision_step("plan", source=self.frames).message
        self.assertRegex(
            text, r"Timing: plan \d+\.\d{3} ms \(highest_confidence \d+\.\d{3} ms\); cycle \d+\.\d{3} ms"
        )

    def test_frame_reports_one_frame_after_replaying_the_ones_before(self) -> None:
        report = self.inspect("plan", frame=2)
        self.assertEqual([item["position"] for item in report["frames"]], [2])
        outside = inspect_decision_step("plan", source=self.frames, frame=3)
        self.assertEqual(outside.exit_code, 2)
        self.assertEqual(outside.message, "--frame 3 is outside the source's 3 frames (0-2).")

    def test_plugin_replaces_the_inspected_steps_selection(self) -> None:
        report = self.inspect("action", plugins=["mode"])
        self.assertEqual(report["selections"]["action"]["plugins"], ["mode"])
        self.assertEqual(report["frames"][0]["record"]["authority"]["gate_id"], "mode")
        unknown = inspect_decision_step("proposal", source=self.frames, plugins=["nope"])
        self.assertEqual(unknown.exit_code, 2)
        self.assertIn("Could not load plugins for proposal inspect:", unknown.message)
        self.assertIn("nope", unknown.message)

    def test_a_recorded_inspect_replays_with_its_selections(self) -> None:
        with patch.dict(os.environ, {"AUTOMA_ACTION_INSPECT_ROOT": str(self.tmp / "inspections")}):
            recorded = self.inspect("action", plugins=["mode"], record=True)
        run_dir = Path(recorded["run_dir"])
        written = sorted(path.relative_to(run_dir).as_posix() for path in run_dir.rglob("*") if path.is_file())
        self.assertEqual(
            written,
            ["frames/frame_000000.png", "frames/frame_000001.png", "frames/frame_000002.png", "report.json"],
        )
        replayed = self.inspect("proposal", source=run_dir)
        self.assertEqual(replayed["source"]["frame_count"], 3)
        self.assertEqual(replayed["selections"]["action"]["plugins"], ["mode"])

    def test_a_recording_that_disabled_a_selectable_step_replays_without_its_plugins(self) -> None:
        """A clearable step the recording left empty replays with no plugins.

        Plan runs one plugin, so a recording that left it empty stays absent.
        """

        replay = StepReplay(_source(), {"steps": dict.fromkeys(SELECTABLE_STEPS)}, steps=SELECTABLE_STEPS)
        for step in SELECTABLE_STEPS:
            with self.subTest(step=step):
                if step in CLEARABLE_STEPS:
                    self.assertEqual(replay.activations[step].plugins, ())
                    self.assertIsNotNone(replay.runners[step])
                else:
                    self.assertIsNone(replay.activations[step])
                    self.assertIsNone(replay.runners[step])

    def test_the_first_frames_selection_builds_the_runners_so_its_restage_keeps_the_map(self) -> None:
        """Regression: a source that records selections only per frame built default
        runners, and frame 0's restage reset them over the map, replacing the ledger."""

        recorded = default_selection("memory").to_payload()
        recorded["plugin_configs"]["bounded_evidence"]["max_records"] = 8
        source = _source({"steps": {"memory": recorded}})
        replay = StepReplay(source, {}, steps=("memory",))
        self.assertEqual(replay.activations["memory"].to_payload(), recorded)
        runner = replay.runners["memory"]
        replay.shared_memory[LEDGER_KEY] = empty_ledger(
            memory_id="memory-7",
            epoch_id="epoch-7",
            bounds=LedgerBounds(max_records=8),
            created_at_ms=0,
            plugin_id="bounded_evidence",
        )

        replay.restage(source.frames[0])

        self.assertIs(replay.runners["memory"], runner)
        self.assertEqual(replay.shared_memory[LEDGER_KEY].epoch_id, "epoch-7")

    def test_cli_has_a_group_per_step_and_no_decision_command(self) -> None:
        for step in DECISION_STEPS:
            with self.subTest(step=step):
                flags = invoke_automa("host", step, "inspect", "--help").stdout
                for flag in ("--plugin", "--frame", "--record", "--json", "--max-frames"):
                    self.assertIn(flag, flags)
        removed = invoke_automa("vehicles", "decision", "help", check=False)
        self.assertEqual(removed.returncode, 2)
        self.assertIn("invalid choice: 'decision'", removed.stderr)


if __name__ == "__main__":
    unittest.main()
