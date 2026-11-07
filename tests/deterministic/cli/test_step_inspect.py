"""Offline plugin step inspect over images and recorded runs."""
from __future__ import annotations

import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from autonomy.decision_cycle.activation import DECISION_STEPS, STEPS, StepActivation, step_activation
from autonomy.decision_cycle.perception.evidence.values import PerceptionEvidenceBatch, PerceptionSignal
from autonomy.decision_cycle.perception.plugin import PerceptionPluginContract
from cli.automa_cli.plugins.inspect.commands import INSPECT_STEPS, inspect_step
from cli.automa_cli.plugins.inspect.replay import StepReplay
from cli.automa_cli.plugins.step_formatting import format_step_record
from cli.automa_cli.replay.models import ImageSource, ReplayFrame
from cli.automa_cli.shared.memory_report import plugin_summaries
from implementations.decision_cycle.catalog import (
    DEFAULT_STEP_PLUGINS,
    DEFAULT_STEP_PRESETS,
    PRESET_STEPS,
    STEP_PRESETS,
    step_plugins,
)
from tests.shared.autonomy.decision_cycle.memory.activation_fixtures import EVIDENCE_SPEC, RECORDING_SPEC
from tests.shared.cli.invoke import invoke_automa
from tests.shared.cli.memory.frame_fixtures import write_frames


_HERE = "tests.deterministic.cli.test_step_inspect"
LOCAL_PERCEPTION = {"local_perception": f"{_HERE}:_LocalPerception"}


class _LocalPerception:
    """Reports one signal; with ``fail`` it raises, as a broken plugin does."""

    plugin_id = "local_perception"
    contract = PerceptionPluginContract()

    def __init__(self, *, fail: bool = False, **_ignored) -> None:
        self.fail = fail

    def perceive(self, inputs) -> PerceptionEvidenceBatch:
        if self.fail:
            raise RuntimeError("plugin failed")
        return PerceptionEvidenceBatch(signals=(PerceptionSignal("local_seen", True),), things=())


def _local(step: str, specs: dict[str, str], configs: dict[str, dict] | None = None) -> StepActivation:
    """An activation of ``step`` that loads these test plugins, in order."""

    return step_activation(step, list(specs), specs, configs)


def _record_steps(frames: Path, steps: dict[int, dict[str, StepActivation]]) -> Path:
    """Have frame N of ``write_frames``' three record the selections ``steps[N]``."""

    (frames / "manifest.json").write_text(json.dumps({"frames": [
        {"image_path": f"frame_{index}.png",
         **({"steps": {step: activation.to_payload() for step, activation in steps[index].items()}}
            if index in steps else {})}
        for index in range(3)
    ]}))
    return frames


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
                self.assertEqual(report["schema"], f"{step}_inspect")
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
        self.assertIn(f"Memory: plugin={frame['memory']['plugins'][0]['plugin_id']}", text)

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
        plan = report["selections"]["plan"]["plugins"]
        plugins = ", ".join(rf"{re.escape(plugin)} \d+\.\d{{3}} ms" for plugin in plan)
        text = inspect_step("plan", source=self.frames).message
        self.assertRegex(text, rf"Timing: plan \d+\.\d{{3}} ms \({plugins}\); cycle \d+\.\d{{3}} ms")

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
                        self.assertNotIn("lines", record)
                        self.assertNotIn("text", record)
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
        both = inspect_step("perception", source=self.frames, preset="not-a-preset", plugins=[])
        self.assertIn("preset or plugins, not both", both.message)

    def test_every_step_names_a_failed_frame_or_recording_and_exits_2(self) -> None:
        for step in INSPECT_STEPS:
            with self.subTest(step=step):
                with patch("cli.automa_cli.plugins.inspect.service.StepReplay.run", side_effect=RuntimeError("broken frame")):
                    result = inspect_step(step, source=self.frames)
                self.assertEqual(result.exit_code, 2)
                self.assertIn("inspect failed at", result.message)
                self.assertIn("broken frame", result.message)
                with patch("cli.automa_cli.plugins.inspect.service.record_inspect_run", side_effect=OSError("disk full")):
                    result = inspect_step(step, source=self.frames, record=True)
                self.assertEqual(result.exit_code, 2)
                self.assertIn(f"Could not record {step} inspect run", result.message)
                self.assertIn("disk full", result.message)

    def test_plugin_failure_is_reported_with_exit_zero(self) -> None:
        # A plugin failure is data; an exception escaping the replay is a command error.
        failing = _local("perception", LOCAL_PERCEPTION, {"local_perception": {"fail": True}})
        report = self.inspect("perception", source=_record_steps(self.frames, {0: {"perception": failing}}))
        run = report["frames"][0]["record"]["plugin_runs"][0]
        self.assertEqual((run["plugin_id"], run["status"]), ("local_perception", "error"))
        self.assertIn("plugin failed", run["error"])
    def test_plugin_replaces_the_inspected_steps_selection_and_its_recording_keeps_it(self) -> None:
        plugin_id = next(plugin for plugin in step_plugins("action") if plugin not in DEFAULT_STEP_PLUGINS["action"])
        with patch.dict(os.environ, {"AUTOMA_ACTION_INSPECT_ROOT": str(self.tmp / "inspections")}):
            recorded = self.inspect("action", plugins=[plugin_id], record=True)
        self.assertEqual(recorded["selections"]["action"]["plugins"], [plugin_id])
        replayed = self.inspect("proposal", source=Path(recorded["run_dir"]))
        self.assertEqual(replayed["selections"]["action"]["plugins"], [plugin_id])
        unknown = inspect_step("proposal", source=self.frames, plugins=["nope"])
        self.assertEqual(unknown.exit_code, 2)
        self.assertIn("Could not load plugins for proposal inspect:", unknown.message)
        self.assertIn("nope", unknown.message)
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

        recorded = _local("memory", {"recording_test": RECORDING_SPEC}).to_payload()
        source = _source({"steps": {"memory": recorded}})
        replay = StepReplay(source, steps=("memory",))
        self.assertEqual(replay.activations["memory"].to_payload(), recorded)
        runner = replay.runners["memory"]
        replay.shared_memory["recording_test.state"] = {"epoch": 7, "records": ()}

        replay.restage(source.frames[0])

        self.assertIs(replay.runners["memory"], runner)
        self.assertEqual(replay.shared_memory["recording_test.state"]["epoch"], 7)

    def test_every_step_restages_the_selection_each_frame_records(self) -> None:
        local = _local("perception", LOCAL_PERCEPTION)
        frames = _record_steps(self.frames, {1: {"perception": local}, 2: {"perception": local}})
        expected = [list(DEFAULT_STEP_PLUGINS["perception"]), ["local_perception"], ["local_perception"]]
        for step in ("perception", "memory"):
            with self.subTest(step=step):
                report = self.inspect(step, source=frames)
                self.assertEqual([item["steps"]["perception"]["plugins"] for item in report["frames"]], expected)
                if step == "perception":
                    self.assertEqual(
                        [[run["plugin_id"] for run in item["record"]["plugin_runs"]] for item in report["frames"]],
                        expected,
                    )
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
    def test_one_image_runs_the_preset_or_plugins_its_flags_name(self) -> None:
        preset = next(name for name in STEP_PRESETS["perception"] if name != DEFAULT_STEP_PRESETS["perception"])
        plugins = list(STEP_PRESETS["perception"][preset]["plugins"])
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "single.jpg"
            Image.new("RGB", (48, 32), (25, 35, 45)).save(image)
            for flags, expected in (({"preset": preset}, plugins), ({"plugins": plugins[:1]}, plugins[:1])):
                with self.subTest(**flags):
                    result = inspect_step("perception", source=image, json_output=True, **flags)
                    self.assertEqual(result.exit_code, 0, result.message)
                    report = json.loads(result.message)
                    self.assertEqual(report["source"]["path"], str(image.resolve()))
                    self.assertEqual(len(report["frames"]), 1)
                    selection = report["selections"]["perception"]
                    self.assertEqual(selection["plugins"], expected)
                    if "preset" in flags:
                        self.assertEqual(selection["preset"], preset)
                    runs = report["frames"][0]["record"]["plugin_runs"]
                    self.assertEqual([run["plugin_id"] for run in runs], expected)

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
        # Both plugins keep their own state; only the first publishes evidence.
        selection = _local(
            "memory",
            {"publishing_test": EVIDENCE_SPEC, "recording_test": RECORDING_SPEC},
            {"publishing_test": {"plugin_id": "publishing_test"}},
        )
        run = _record_steps(write_frames(self.tmp / "run"), dict.fromkeys(range(3), {"memory": selection}))
        report = self.inspect(run)
        self.assertEqual(report["selections"]["memory"]["plugins"], ["publishing_test", "recording_test"])
        for item in report["frames"]:
            rows = {plugin["plugin_id"]: plugin for plugin in plugin_summaries(item["record"])}
            self.assertEqual(list(rows), ["publishing_test", "recording_test"])
            self.assertIsNone(rows["recording_test"]["health"])
            self.assertEqual(rows["recording_test"]["record_count"], 1)
            self.assertEqual(item["record"]["evidence_publisher"], "publishing_test")

        text = inspect_step("memory", source=str(run)).message
        self.assertIn("Frame 2:", text)
        self.assertIn("Memory: plugin=publishing_test health=None records=1 epoch=epoch-1", text)
        self.assertIn("Memory: plugin=recording_test health=None records=1 epoch=epoch-1", text)
        self.assertIn("Memory: publisher=publishing_test", text)
