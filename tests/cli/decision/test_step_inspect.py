"""Offline proposal, plan, and action inspect over images and recorded runs."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autonomy.decision_cycle.activation import DECISION_STEPS
from cli.automa_cli.decision_steps import inspect_decision_step
from tests.cli.memory.test_inspect import write_frames
from tests.support.cli_runner import run_automa


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

    def test_cli_has_a_group_per_step_and_no_decision_command(self) -> None:
        for step in DECISION_STEPS:
            with self.subTest(step=step):
                flags = run_automa("vehicles", step, "inspect", "--help").stdout
                for flag in ("--plugin", "--frame", "--record", "--json", "--max-frames"):
                    self.assertIn(flag, flags)
        removed = run_automa("vehicles", "decision", "help", check=False)
        self.assertEqual(removed.returncode, 2)
        self.assertIn("invalid choice: 'decision'", removed.stderr)


if __name__ == "__main__":
    unittest.main()
