"""Committing a frame appends one line and leaves earlier frames readable."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.cycle import DecisionSteps
from autonomy.runtime import recording as recording_module
from autonomy.runtime.cycle_host import AutonomyCycleHost
from autonomy.runtime.recording import (
    RECORDING_ENTRIES_NAME,
    RECORDING_MANIFEST_NAME,
    RunRecording,
)
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorFrame, SensorReading
from tests.shared.autonomy.runtime.recording_fixtures import read_recording_entries


def _camera(root: Path) -> Path:
    camera = root / "camera.png"
    Image.new("RGB", (2, 2)).save(camera)
    return camera


def _append_frame(host: AutonomyCycleHost, recording: RunRecording, camera: Path, index: int):
    sensors = SensorFrame(
        read_id=f"f{index}", readings={FRONT_CAMERA_SENSOR_ID: SensorReading(
            sensor_id=FRONT_CAMERA_SENSOR_ID, sensor_kind="camera",
            captured_at_ms=index, path=str(camera),
        )}, started_at_ms=index, completed_at_ms=index,
    )
    result = host.run(DecisionFrameContext(f"f{index}", index, index, sensor_frame=sensors))
    recording.append(result, ())
    return result


class RecordingAppendTests(unittest.TestCase):
    def test_each_commit_keeps_earlier_bytes_and_read_returns_that_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            camera = _camera(root)
            host = AutonomyCycleHost(steps=DecisionSteps())
            self.addCleanup(host.close)
            recording = RunRecording(root, host_name="picar", run_id="append-run")
            entries_path = recording.root / RECORDING_ENTRIES_NAME
            header_path = recording.root / RECORDING_MANIFEST_NAME
            previous = b""
            header = b""
            frame_ids: list[str] = []
            for index in range(5):
                result = _append_frame(host, recording, camera, index)
                frame_ids.append(result.context.frame_id)
                current = entries_path.read_bytes()
                self.assertTrue(current.startswith(previous))
                added = current[len(previous):]
                self.assertTrue(added.endswith(b"\n"))
                self.assertEqual(added.count(b"\n"), 1)
                self.assertEqual(json.loads(added)["frame_id"], frame_ids[-1])
                previous = current
                if index == 0:
                    header = header_path.read_bytes()
                    declared = json.loads(header)
                    self.assertEqual(declared["entries"], RECORDING_ENTRIES_NAME)
                    self.assertNotIn("frames", declared)
                    self.assertNotIn("steps", declared)
                else:
                    self.assertEqual(header_path.read_bytes(), header)
                shown = recording.read(run_id=recording.run_id, after=index)
                self.assertEqual(shown["after"], index)
                self.assertEqual(shown["recorded_count"], index + 1)
                self.assertEqual(
                    [item["frame"]["frame_id"] for item in shown["frames"]],
                    frame_ids[index:],
                )
            self.assertEqual(
                [item["frame_id"] for item in read_recording_entries(recording.root)],
                frame_ids,
            )
            tail = recording.read(run_id=recording.run_id, after=3)
            self.assertEqual([item["frame"]["frame_id"] for item in tail["frames"]], frame_ids[3:])
            empty = recording.read(run_id=recording.run_id, after=5)
            self.assertEqual(empty["frames"], [])
            self.assertEqual(empty["recorded_count"], 5)
            with self.assertRaisesRegex(ValueError, "recording cursor is outside the committed history"):
                recording.read(run_id=recording.run_id, after=6)
            # A transport consumer must not be able to change the retained change.
            first = recording.read(run_id=recording.run_id, after=0)
            first["frames"][0]["frame"]["control_application_change"]["kept"] = 99
            again = recording.read(run_id=recording.run_id, after=0)
            self.assertEqual(again["frames"][0]["frame"]["control_application_change"]["kept"], 0)

    def test_refused_and_incomplete_appends_leave_committed_frames_readable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            camera = _camera(root)
            host = AutonomyCycleHost(steps=DecisionSteps())
            self.addCleanup(host.close)
            recording = RunRecording(root, host_name="picar", run_id="append-run")
            entries_path = recording.root / RECORDING_ENTRIES_NAME
            header = b""
            results = [_append_frame(host, recording, camera, index) for index in range(2)]
            header = (recording.root / RECORDING_MANIFEST_NAME).read_bytes()
            frame_ids = [result.context.frame_id for result in results]
            committed = entries_path.read_bytes()

            with self.assertRaisesRegex(ValueError, "recorded frame_id must be unique within the run"):
                recording.append(results[0], ())
            item = recording.read(run_id=recording.run_id, after=0)["frames"][0]
            with self.assertRaisesRegex(ValueError, "recorded frame_id must be unique within the run"):
                recording.writer.commit(item["frame"], camera.read_bytes(), ".png")
            self.assertEqual(entries_path.read_bytes(), committed)
            self.assertEqual((recording.root / RECORDING_MANIFEST_NAME).read_bytes(), header)
            self.assertEqual(recording.count, 2)

            original_write = recording_module.os.write

            def interrupted(descriptor: int, data: bytes) -> int:
                original_write(descriptor, data[:4])
                raise OSError("interrupted")

            with patch.object(recording_module.os, "write", interrupted):
                with self.assertRaises(OSError):
                    recording_module.append_recording_entry(entries_path, {"frame_id": "lost"})
            self.assertEqual(entries_path.read_bytes(), committed)

            with entries_path.open("ab") as handle:
                handle.write(b'{"frame_id":"torn"')
            self.assertEqual(
                [entry["frame_id"] for entry in read_recording_entries(recording.root)],
                frame_ids,
            )
            readable = recording.read(run_id=recording.run_id, after=0)
            self.assertEqual([item["frame"]["frame_id"] for item in readable["frames"]], frame_ids)


if __name__ == "__main__":
    unittest.main()
