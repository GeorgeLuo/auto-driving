"""A recording stores each generation once, and replay shares it across frames."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.steps import decision_steps
from autonomy.runtime.cycle_host import AutonomyCycleHost
from autonomy.runtime.recording import RECORDING_ENTRIES_NAME, RecordingWriter, RunRecording
from autonomy.runtime.session import RunConfiguration
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorFrame, SensorReading
from cli.automa_cli.hosts.runs.models import RunState
from cli.automa_cli.hosts.runs.monitor import _sync_recording
from cli.automa_cli.plugins.inspect.replay import StepReplay
from cli.automa_cli.replay.source import load_image_source
from implementations.runtime.picar.control import DonkeyControlTarget
from tests.shared.autonomy.runtime.recording_fixtures import (
    read_recording_entries, read_recording_generations,
)
from tests.support.perception_fixtures import local_perception


def _context(camera: Path, index: int) -> DecisionFrameContext:
    sensors = SensorFrame(
        read_id=f"f{index}", readings={FRONT_CAMERA_SENSOR_ID: SensorReading(
            sensor_id=FRONT_CAMERA_SENSOR_ID, sensor_kind="camera",
            captured_at_ms=1000 + index, path=str(camera),
        )}, started_at_ms=1000 + index, completed_at_ms=1000 + index,
    )
    return DecisionFrameContext(f"f{index}", index, 1000 + index, sensor_frame=sensors)


def _recording(root: Path, perception) -> tuple[AutonomyCycleHost, RunRecording]:
    host = AutonomyCycleHost(
        steps=decision_steps({"perception": perception}), target=DonkeyControlTarget(),
    )
    recording = RunRecording(root, host_name="picar", run_id="generations")
    host.start(RunConfiguration(mode="observe_only", num_decisions=None), recording=recording)
    return host, recording


class RecordingGenerationTests(unittest.TestCase):
    def test_a_generation_change_is_stored_once_and_replayed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            camera = root / "camera.png"
            Image.new("RGB", (2, 2)).save(camera)
            host, recording = _recording(root, local_perception("a", "b"))
            self.addCleanup(host.close)
            host.run(_context(camera, 0))
            host.run(_context(camera, 1))
            self.assertTrue(host.arm({"perception": local_perception("a", "b", "c").to_payload()})["ok"])
            host.run(_context(camera, 2))

            files = list((recording.root / "generations").glob("*.json"))
            self.assertEqual(len(files), 2)
            entries = read_recording_entries(recording.root)
            self.assertEqual([entry["frame_id"] for entry in entries], ["f0", "f1", "f2"])
            for entry in entries:
                self.assertNotIn("steps", entry)
                self.assertNotIn("step_activations", entry)
                self.assertNotIn("step_activations", entry["context"]["metadata"])
                self.assertNotIn("plugin_specs", json.dumps(entry))
            self.assertEqual(entries[0]["generation_id"], entries[1]["generation_id"])
            self.assertNotEqual(entries[0]["generation_id"], entries[2]["generation_id"])

            export = root / "export"
            state = RunState(host_name="picar")

            class Client:
                def read_recording(self, run_id, *, after):
                    return recording.read(run_id=run_id, after=after)

            writer = RecordingWriter(export, host_name="picar", run_id=recording.run_id)
            _sync_recording(Client(), state, writer=writer)
            self.assertEqual(state.recorded_count, recording.count)
            self.assertEqual(
                (export / "manifest.json").read_bytes(),
                (recording.root / "manifest.json").read_bytes(),
            )
            self.assertEqual(
                (export / RECORDING_ENTRIES_NAME).read_bytes(),
                (recording.root / RECORDING_ENTRIES_NAME).read_bytes(),
            )
            self.assertEqual(read_recording_generations(export), read_recording_generations(recording.root))

            source = load_image_source(recording.root)
            self.assertIs(source.frames[0].selections, source.frames[1].selections)
            self.assertIsNot(source.frames[0].selections, source.frames[2].selections)
            self.assertEqual(source.frames[0].selections["perception"].plugins, ("a", "b"))
            self.assertEqual(source.frames[2].selections["perception"].plugins, ("a", "b", "c"))
            self.assertNotIn("steps", source.frames[0].metadata)

    def test_a_preset_rename_keeps_the_generation_and_the_labels_of_its_first_frame(self) -> None:
        # A display-only change does not restage live or in replay, so its frames
        # replay under the generation's first labels.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            camera = root / "camera.png"
            Image.new("RGB", (2, 2)).save(camera)
            host, recording = _recording(root, local_perception("a", "b", preset="earlier"))
            self.addCleanup(host.close)
            host.run(_context(camera, 0))
            self.assertTrue(host.arm({
                "perception": local_perception("a", "b", preset="later").to_payload(),
            })["ok"])
            host.run(_context(camera, 1))

            self.assertEqual(len(list((recording.root / "generations").glob("*.json"))), 1)
            lines = (recording.root / RECORDING_ENTRIES_NAME).read_text().splitlines()
            self.assertEqual([json.loads(line)["frame_id"] for line in lines], ["f0", "f1"])
            entries = read_recording_entries(recording.root)
            self.assertEqual(entries[0]["generation_id"], entries[1]["generation_id"])

            source = load_image_source(recording.root)
            self.assertIs(source.frames[0].selections, source.frames[1].selections)
            self.assertEqual(source.frames[1].selections["perception"].metadata["preset"], "earlier")
            replay = StepReplay(source, steps=("perception",))
            replay.shared_memory["kept"] = True
            runner = replay.runners["perception"]
            for frame in source.frames:
                replay.restage(frame)
                self.assertEqual(replay.activations["perception"].metadata["preset"], "earlier")
            self.assertIs(replay.runners["perception"], runner)
            self.assertTrue(replay.shared_memory["kept"])


if __name__ == "__main__":
    unittest.main()
