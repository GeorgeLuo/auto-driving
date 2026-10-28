"""Offline plugin step inspect over images and recorded runs."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from PIL import Image
from dataclasses import replace
from cli.automa_cli.hosts.inspection_runs import selection_report
from cli.automa_cli.hosts.memory_report import plugin_summaries
from implementations.decision_cycle.perception.presets import DEFAULT_PERCEPTION_PRESET
from tests.shared.autonomy.decision_cycle.memory.activation_fixtures import RECORDING_SPEC
from implementations.decision_cycle.catalog import selection_activation
from cli.automa_cli.hosts.workbench_source import normalize_image_directory
from unittest.mock import patch

from autonomy.decision_cycle.activation import DECISION_STEPS, STEPS
from cli.automa_cli.hosts.step_formatting import format_step_record
from cli.automa_cli.plugins.step_inspect import INSPECT_STEPS, inspect_step
from cli.automa_cli.plugins.step_replay import StepReplay, default_selection
from cli.automa_cli.hosts.workbench_source import ImageSource, ReplayFrame
from implementations.decision_cycle.catalog import PRESET_STEPS
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


class StepInspectTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.frames = write_frames(self.tmp / "frames")

    def inspect(self, step: str, source: Path | None = None, **options) -> dict:
        result = inspect_step(step, source=source or self.frames, json_output=True, **options)
        self.assertEqual(result.exit_code, 0, result.message)
        return json.loads(result.message)

    def test_each_step_reports_its_record_for_every_frame(self) -> None:
        for step in INSPECT_STEPS:
            with self.subTest(step=step):
                report = self.inspect(step)
                self.assertEqual(set(report), {
                    "schema", "step", "run_id", "source_id", "source", "selections", "frames", "run_dir",
                })
                self.assertEqual(report["step"], step)
                self.assertEqual(report["schema"], f"{step}_inspect_v1")
                self.assertEqual(report["source"]["frame_count"], 3)
                self.assertEqual([item["position"] for item in report["frames"]], [0, 1, 2])
                for item in report["frames"]:
                    self.assertTrue({
                        "position", "frame_id", "frame_index", "timestamp_ms", "image_path",
                        "absence_reason", "record", "plugin_report", "timing", "steps",
                    }.issubset(item))
                    self.assertIsNotNone(item["record"], item)
                    self.assertIn("plugins" if step == "memory" else "status", item["record"])
                self.assertEqual(
                    set(report["selections"]),
                    {"perception", "observation", "memory", *DECISION_STEPS},
                )
                text = inspect_step(step, source=self.frames).message
                self.assertIn(f"{step.capitalize()} inspect", text)
                self.assertIn("Memory: plugin=" if step == "memory" else f"{step.capitalize()}: status=", text)

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
        text = inspect_step("proposal", source=self.frames).message
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
        text = inspect_step("plan", source=self.frames).message
        self.assertRegex(
            text, r"Timing: plan \d+\.\d{3} ms \(highest_confidence \d+\.\d{3} ms\); cycle \d+\.\d{3} ms"
        )

    def test_each_step_reports_one_frame_after_replaying_its_history(self) -> None:
        for step in INSPECT_STEPS:
            with self.subTest(step=step):
                report = self.inspect(step, frame=2)
                self.assertEqual([item["position"] for item in report["frames"]], [2])
                # Timing is incidental; the record must be the same as a full replay's last frame.
                expected = self.inspect(step)["frames"][2]["record"]
                actual = report["frames"][0]["record"]
                if step == "perception":
                    for record in (actual, expected):
                        record.pop("lines")
                        record.pop("text")
                        for run in record["plugin_runs"]:
                            run.pop("duration_ms")
                self.assertEqual(actual, expected)
                for frame in (-1, 3):
                    outside = inspect_step(step, source=self.frames, frame=frame)
                    self.assertEqual(outside.exit_code, 2)
                    self.assertIn("outside the source's 3 frames", outside.message)

    def test_every_step_records_only_reported_frames_and_replays_as_another_step(self) -> None:
        for step in INSPECT_STEPS:
            with self.subTest(step=step), patch.dict(os.environ, {
                f"AUTOMA_{step.upper()}_INSPECT_ROOT": str(self.tmp / step),
            }):
                plain = self.inspect(step)
                self.assertIsNone(plain["run_dir"])
                self.assertFalse((self.tmp / step).exists())
                recorded = self.inspect(step, record=True)
                run_dir = Path(recorded["run_dir"])
                self.assertEqual(run_dir.parent, self.tmp / step)
                self.assertEqual(json.loads((run_dir / "report.json").read_text()), recorded)
                written = sorted(path.relative_to(run_dir).as_posix() for path in run_dir.rglob("*") if path.is_file())
                self.assertEqual(written, [
                    "frames/frame_000000.png", "frames/frame_000001.png", "frames/frame_000002.png", "manifest.json", "report.json",
                ])
                replayed = self.inspect("action" if step != "action" else "perception", source=run_dir)
                self.assertEqual(replayed["selections"], recorded["selections"])
                self.assertEqual([item["frame_id"] for item in replayed["frames"]],
                                 [item["frame_id"] for item in recorded["frames"]])
                selected = self.inspect(step, record=True, frame=1)
                self.assertEqual(len(list((Path(selected["run_dir"]) / "frames").iterdir())), 2)
                self.assertEqual(self.inspect("action", source=Path(selected["run_dir"]))["source"]["frame_count"], 2)

    def test_absent_frames_have_no_record_for_every_step_and_replay_as_gaps(self) -> None:
        (self.frames / "manifest.json").write_text(json.dumps({"frames": [
            {"image_path": "frame_0.png"},
            {"frame_id": "gap", "absence_reason": "camera missing"},
            {"image_path": "frame_2.png"},
        ]}))
        for step in INSPECT_STEPS:
            with self.subTest(step=step), patch.dict(os.environ, {
                f"AUTOMA_{step.upper()}_INSPECT_ROOT": str(self.tmp / step),
            }):
                report = self.inspect(step, record=True)
                gap = report["frames"][1]
                self.assertIsNone(gap["record"])
                self.assertIsNone(gap["image_path"])
                self.assertEqual(gap["absence_reason"], "camera missing")
                self.assertIsNotNone(report["frames"][2]["record"])
                replayed = self.inspect("action", source=Path(report["run_dir"]))
                self.assertIsNone(replayed["frames"][1]["record"])
                self.assertIn("Absent: camera missing", inspect_step(step, source=self.frames).message)

    def test_every_step_exits_2_for_unreadable_inputs_and_plugin_load_errors(self) -> None:
        empty = self.tmp / "empty"
        empty.mkdir()
        for step in INSPECT_STEPS:
            for options in (
                {"source": self.tmp / "missing"}, {"source": empty},
                {"shared_memory_file": self.tmp / "missing.json"},
                {"max_frames": 0}, {"max_frames": 2},
                {"plugins": ["not-a-plugin"]}, {"preset": "not-a-preset"},
                {"preset": "not-a-preset", "plugins": []},
            ):
                with self.subTest(step=step, options=options):
                    result = inspect_step(step, **{"source": self.frames, **options})
                    self.assertEqual(result.exit_code, 2, result.message)

    def test_every_step_names_a_failed_frame_or_recording_and_exits_2(self) -> None:
        for step in INSPECT_STEPS:
            with self.subTest(step=step):
                with patch("cli.automa_cli.plugins.step_inspect.StepReplay.run", side_effect=RuntimeError("broken frame")):
                    result = inspect_step(step, source=self.frames)
                self.assertEqual(result.exit_code, 2)
                self.assertIn("inspect failed at", result.message)
                self.assertIn("broken frame", result.message)
                with patch("cli.automa_cli.plugins.step_inspect.record_inspect_run", side_effect=OSError("disk full")):
                    result = inspect_step(step, source=self.frames, record=True)
                self.assertEqual(result.exit_code, 2)
                self.assertIn(f"Could not record {step} inspect run", result.message)
                self.assertIn("disk full", result.message)

    def test_plugin_failure_is_reported_with_exit_zero(self) -> None:
        # A plugin failure is data; an exception escaping the replay is a command error.
        with patch(
            "implementations.decision_cycle.perception.plugins.frame.plugin.FrameObservationPlugin.perceive",
            side_effect=RuntimeError("plugin failed"),
        ):
            report = self.inspect("perception", plugins=["frame"])
        run = report["frames"][0]["record"]["plugin_runs"][0]
        self.assertEqual(run["status"], "error")
        self.assertIn("plugin failed", run["error"])

    def test_plugin_replaces_the_inspected_steps_selection(self) -> None:
        report = self.inspect("action", plugins=["mode"])
        self.assertEqual(report["selections"]["action"]["plugins"], ["mode"])
        self.assertEqual(report["frames"][0]["record"]["authority"]["gate_id"], "mode")
        unknown = inspect_step("proposal", source=self.frames, plugins=["nope"])
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
            ["frames/frame_000000.png", "frames/frame_000001.png", "frames/frame_000002.png", "manifest.json", "report.json"],
        )
        replayed = self.inspect("proposal", source=run_dir)
        self.assertEqual(replayed["source"]["frame_count"], 3)
        self.assertEqual(replayed["selections"]["action"]["plugins"], ["mode"])

    def test_a_recording_that_disabled_a_selectable_step_replays_without_its_plugins(self) -> None:
        """A recorded absent slot stays absent, even when its step is clearable."""
        replay = StepReplay(_source({"steps": dict.fromkeys(PRESET_STEPS)}), steps=PRESET_STEPS)
        for step in PRESET_STEPS:
            with self.subTest(step=step):
                self.assertIsNone(replay.activations[step])
                self.assertIsNone(replay.runners[step])

    def test_the_first_frames_selection_builds_the_runners_so_its_restage_keeps_the_map(self) -> None:
        """Regression: a source that records selections only per frame built default
        runners, and frame 0's restage reset them over the map, replacing the ledger."""

        recorded = default_selection("memory").to_payload()
        recorded["plugin_configs"]["bounded_evidence"]["max_records"] = 8
        source = _source({"steps": {"memory": recorded}})
        replay = StepReplay(source, steps=("memory",))
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

    def test_cli_has_one_inspect_group_and_no_decision_command(self) -> None:
        for step in INSPECT_STEPS:
            with self.subTest(step=step):
                flags = invoke_automa("plugins", "inspect", step, "--help").stdout
                for flag in ("--plugin", "--frame", "--record", "--json", "--max-frames", "--shared-memory"):
                    self.assertIn(flag, flags)
                for removed in ("--name", "--frames", "--interval-s", "--timeout-s"):
                    self.assertNotIn(removed, flags)
                old_order = invoke_automa("plugins", step, "inspect", "--help", check=False)
                self.assertEqual(old_order.returncode, 2)
                removed_host = invoke_automa("hosts", step, "inspect", "--help", check=False)
                self.assertEqual(removed_host.returncode, 2)
        removed = invoke_automa("vehicles", "decision", "help", check=False)
        self.assertEqual(removed.returncode, 2)
        self.assertIn("invalid choice: 'decision'", removed.stderr)


class PerceptionInspectTests(unittest.TestCase):
    def test_inspect_runs_selected_catalog_plugins_on_one_image(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "single.jpg"
            Image.new("RGB", (48, 32), (25, 35, 45)).save(image)
            result = inspect_step(
                "perception", source=image,
                plugins=["frame", "classical_regions"],
                json_output=True,
            )

        self.assertEqual(result.exit_code, 0, result.message)
        report = json.loads(result.message)
        self.assertEqual(report["source"]["path"], str(image.resolve()))
        self.assertEqual(len(report["frames"]), 1)
        runs = report["frames"][0]["record"]["plugin_runs"]
        self.assertEqual([run["plugin_id"] for run in runs], ["frame", "classical_regions"])

    def test_inspect_rejects_preset_and_plugins_together(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "single.jpg"
            Image.new("RGB", (48, 32), (25, 35, 45)).save(image)
            both = inspect_step("perception", source=image, preset="visual_observer", plugins=["frame"])

        self.assertEqual(both.exit_code, 2)
        self.assertIn("preset or plugins, not both", both.message)

    def test_inspect_reports_the_preset_or_custom_selection_it_applied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "single.jpg"
            Image.new("RGB", (48, 32), (25, 35, 45)).save(image)
            preset = inspect_step("perception", source=image, preset="visual_observer", json_output=True)
            custom = inspect_step("perception", source=image, plugins=["frame"], json_output=True)

        self.assertEqual(json.loads(preset.message)["selections"]["perception"]["preset"], "visual_observer")
        self.assertEqual(json.loads(custom.message)["selections"]["perception"]["preset"], "custom")
        self.assertEqual(json.loads(custom.message)["selections"]["perception"]["plugins"], ["frame"])

    def test_inspect_restages_perception_from_frame_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            Image.new("RGB", (48, 32), (25, 35, 45)).save(root / "a.png")
            Image.new("RGB", (48, 32), (30, 40, 50)).save(root / "b.png")
            switched = selection_activation("perception", plugins=["frame"])
            manifest = {
                "source_id": "restage-source",
                "frames": [
                    {"image_path": "a.png"},
                    {
                        "image_path": "b.png",
                        "steps": {"perception": switched.to_payload()},
                    },
                ],
            }
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            result = inspect_step("perception", source=root, json_output=True)

        self.assertEqual(result.exit_code, 0, result.message)
        report = json.loads(result.message)
        self.assertEqual(
            [run["plugin_id"] for run in report["frames"][0]["record"]["plugin_runs"]],
            ["frame", "floor_plane"],
        )
        self.assertEqual(
            [run["plugin_id"] for run in report["frames"][1]["record"]["plugin_runs"]],
            ["frame"],
        )

    def test_inspect_manifest_remaps_archived_frame_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            frames.mkdir()
            archived = frames / "frame_000000.png"
            Image.new("RGB", (8, 8), (10, 20, 30)).save(archived)
            manifest = {
                "run_dir": "/original/machine/run",
                "frames": [
                    {
                        "image_path": "/original/machine/run/frames/frame_000000.png",
                    }
                ]
            }

            (root / "report.json").write_text(json.dumps(manifest))
            paths = [frame.image_path for frame in normalize_image_directory(root).frames]

        self.assertEqual(paths, [archived.resolve()])

    def test_startup_report_inspect_preserves_before_after_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            frames.mkdir()
            after = frames / "00_after.png"
            before = frames / "00_before.png"
            Image.new("RGB", (8, 8), (20, 20, 20)).save(after)
            Image.new("RGB", (8, 8), (10, 10, 10)).save(before)
            manifest = {
                "results": [
                    {
                        "before_capture": {"path": str(before)},
                        "after_capture": {"path": str(after)},
                    }
                ]
            }

            (root / "report.json").write_text(json.dumps(manifest))
            paths = [frame.image_path for frame in normalize_image_directory(root).frames]

        self.assertEqual(
            [path.name for path in paths], ["00_before.png", "00_after.png"]
        )


class MemoryInspectTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.frames = write_frames(self.tmp / "frames")
        self.root = self.tmp / "inspections"
        patcher = patch.dict(os.environ, {"AUTOMA_MEMORY_INSPECT_ROOT": str(self.root)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def inspect(self, source=None, **options) -> dict:
        result = inspect_step("memory", source=str(source or self.frames), json_output=True, **options)
        self.assertEqual(result.exit_code, 0, result.message)
        return json.loads(result.message)

    def test_two_recorded_plugins_each_report_and_the_publisher_is_named(self) -> None:
        # The recording plugin runs after bounded_evidence and keeps its own state.
        bounded = selection_activation("memory", plugins=["bounded_evidence"])
        run = write_frames(self.tmp / "run")
        selection = replace(
            bounded, plugins=("bounded_evidence", "recording_test"),
            plugin_specs={**dict(bounded.plugin_specs), "recording_test": RECORDING_SPEC},
            plugin_configs={**dict(bounded.plugin_configs), "recording_test": {}},
        )
        (run / "report.json").write_text(json.dumps({
            "schema": "memory_inspect_v1",
            "selections": {"memory": selection_report(selection)},
            "frames": [{"image_path": f"frame_{index}.png", "steps": {"memory": selection.to_payload()}}
                       for index in range(3)],
        }))
        report = self.inspect(run)
        self.assertEqual(report["selections"]["memory"]["plugins"], ["bounded_evidence", "recording_test"])
        for item in report["frames"]:
            rows = {plugin["plugin_id"]: plugin for plugin in plugin_summaries(item["record"])}
            self.assertEqual(list(rows), ["bounded_evidence", "recording_test"])
            self.assertIsNone(rows["recording_test"]["health"])
            self.assertEqual(rows["recording_test"]["record_count"], 1)
            self.assertEqual(item["record"]["evidence_publisher"], "bounded_evidence")

        text = inspect_step("memory", source=str(run)).message
        self.assertIn("Frame 2:", text)
        self.assertIn("Memory: plugin=bounded_evidence health=healthy", text)
        self.assertIn("Memory: plugin=recording_test health=None records=1 epoch=epoch-1", text)
        self.assertIn("Memory: publisher=bounded_evidence", text)

    def test_perception_restores_the_selection_a_recorded_run_names(self) -> None:
        plain = self.inspect()
        self.assertEqual(plain["selections"]["perception"]["preset"], DEFAULT_PERCEPTION_PRESET)
        self.assertNotIn("multi_obstruction_tracks", plain["selections"]["perception"]["plugins"])

        selection = selection_activation("perception", preset="multi_obstruction")
        run = write_frames(self.tmp / "run")
        (run / "report.json").write_text(json.dumps({
            "schema": "perception_inspect_v1",
            "selections": {"perception": selection_report(selection)},
            "frames": [{
                "image_path": f"frame_{index}.png", "timestamp_ms": 5000 + 400 * index,
                "steps": {"perception": selection.to_payload()},
            } for index in range(3)],
        }))
        report = self.inspect(run, plugins=["bounded_evidence"])
        self.assertEqual(report["selections"]["perception"]["preset"], "multi_obstruction")
        self.assertEqual(report["selections"]["perception"]["plugins"], list(selection.plugins))
        retained = [
            record["record_id"] for record in report["frames"][-1]["record"]["plugins"][0]["state"]["records"]
        ]
        self.assertTrue(
            any(":multi_obstruction_tracks:" in record_id for record_id in retained), retained
        )
