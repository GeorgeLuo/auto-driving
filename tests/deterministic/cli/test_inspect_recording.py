"""An inspect archive preserves the inputs used to execute its reported frames."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

from autonomy.runtime.control import AutonomyControl
from autonomy.runtime.execution import ControlApplication
from cli.automa_cli.plugins.step_inspect import inspect_step
from cli.automa_cli.plugins.step_replay import read_replay_source
from tests.shared.cli.memory.frame_fixtures import write_frames


class InspectRecordingTests(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.temp)
        self.enterContext(patch.dict(os.environ, {
            f"AUTOMA_{step}_INSPECT_ROOT": str(self.root / step)
            for step in ("PERCEPTION", "MEMORY", "ACTION")
        }))

    def inspect(self, step, source, **kwargs):
        result = inspect_step(step, source=source, json_output=True, **kwargs)
        self.assertEqual(result.exit_code, 0, result.message)
        return json.loads(result.message)

    def test_recording_preserves_host_inputs_cycle_time_and_application_windows(self):
        source = write_frames(self.root / "source", count=4)
        applications = [ControlApplication(
            frame_id=f"write-{index}", mode="autonomy", applied=True,
            reason="test", control=AutonomyControl(steering=.2, throttle=.5), delivered_at_ms=at,
        ).to_dict() for index, at in enumerate((100, 100, 90))]
        entries = []
        for index in range(4):
            entries.append({
                "image_path": f"frame_{index}.png", "mode": "autonomy",
                "user_steering": .25, "user_throttle": .5,
                "context": {"timestamp_ms": 5000 + index * 1000},
                **({"control_application_change": {"kept": 0, "added": applications[:index-1]}}
                   if index else {}),
            })
        (source / "manifest.json").write_text(json.dumps({"frames": entries}))
        report = self.inspect("action", source, plugins=["mode"], record=True)
        archive = Path(report["run_dir"])
        replayed = self.inspect("action", archive)
        self.assertTrue(report["frames"][0]["record"]["authority"]["proposed_applied"])
        self.assertEqual([frame["record"] for frame in report["frames"]],
                         [frame["record"] for frame in replayed["frames"]])
        before = read_replay_source(source, max_frames=512)
        after = read_replay_source(archive, max_frames=512)
        for first, second in zip(before.frames, after.frames):
            self.assertEqual((first.mode, first.user_steering, first.user_throttle, first.control_applications),
                             (second.mode, second.user_steering, second.user_throttle, second.control_applications))
            self.assertEqual(first.metadata["context"], second.metadata["context"])
        self.assertIsNone(after.frames[0].control_applications)
        self.assertEqual(after.frames[1].control_applications, ())
        # Include the entire equal/backwards-clock window in a separate recording.
        entries[-1]["control_application_change"]["added"] = applications
        (source / "manifest.json").write_text(json.dumps({"frames": entries}))
        report = self.inspect("action", source, record=True)
        loaded = read_replay_source(report["run_dir"], max_frames=512)
        self.assertEqual([a.delivered_at_ms for a in loaded.frames[-1].control_applications], [100, 100, 90])

    def test_selected_frame_keeps_warmup_prefix_after_archive_moves(self):
        source = self.root / "images"
        source.mkdir()
        rgb = np.random.default_rng(7).integers(0, 256, (72, 96, 3), dtype=np.uint8)
        for index in range(3):
            Image.fromarray(np.roll(rgb, index * 2, axis=1)).save(source / f"{index}.png")
        report = self.inspect("perception", source, plugins=["motion_tracks"], frame=1, record=True)
        self.assertEqual(len(report["frames"]), 1)
        self.assertEqual(report["frames"][0]["record"]["plugin_runs"][0]["status"], "ok")
        archive = self.root / "moved"
        shutil.move(report["run_dir"], archive)
        shutil.rmtree(source)
        manifest = json.loads((archive / "manifest.json").read_text())
        self.assertEqual(len(manifest["frames"]), 2)
        replayed = self.inspect("perception", archive, frame=1)
        for key in ("signals", "things", "measurements"):
            self.assertEqual(report["frames"][0]["record"][key], replayed["frames"][0]["record"][key])
        self.assertEqual(replayed["frames"][0]["record"]["plugin_runs"][0]["status"], "ok")
