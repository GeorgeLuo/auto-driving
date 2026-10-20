"""The application stream is stored once and reconstructs the plugin inputs."""
from __future__ import annotations

import base64
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from autonomy.decision_cycle.context import CONTROL_APPLICATION_RESET_KEY, DecisionFrameContext
from autonomy.decision_cycle.cycle import DecisionSteps
from autonomy.runtime.cycle_host import AutonomyCycleHost
from autonomy.runtime.control import AutonomyControl
from autonomy.runtime.execution import CONTROL_APPLICATION_LIMIT, ControlApplication
from autonomy.runtime.recording import RECORDING_MANIFEST_NAME, RunRecording, write_recorded_frame
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorFrame, SensorReading
from cli.automa_cli.workbench_frames import run_frame
from cli.automa_cli.workbench_source import SourceValidationError, load_image_source
from implementations.runtime.picar.control import DonkeyControlTarget
from tests.shared.cli.workbench_fixtures import image_source, write_manifest


class ControlApplicationRecordingTests(unittest.TestCase):
    def test_recording_and_export_store_deltas_once_and_replay_reset_windows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            camera = root / "camera.png"
            Image.new("RGB", (2, 2)).save(camera)
            recording = RunRecording(root, vehicle_id="picar", run_id="test-run")
            host = AutonomyCycleHost(steps=DecisionSteps(), target=DonkeyControlTarget())
            self.addCleanup(host.close)
            expected = []
            with patch("autonomy.runtime.execution.time.time", return_value=2.0):
                host.start(recording=recording)
                for index in range(4):
                    if index == 1:
                        # Two equal JSON records must both survive, even after a
                        # backwards clock step. They are distinct applications.
                        with patch("autonomy.runtime.execution.time.time", return_value=1.0):
                            host.execution._write_idle("same")
                            host.execution._write_idle("same")
                    if index == 2:
                        host.reset_memory()
                    sensors = SensorFrame(
                        read_id=f"f{index}", readings={FRONT_CAMERA_SENSOR_ID: SensorReading(
                            sensor_id=FRONT_CAMERA_SENSOR_ID, sensor_kind="camera",
                            captured_at_ms=2000 + index, path=str(camera),
                        )}, started_at_ms=2000 + index, completed_at_ms=2000 + index,
                    )
                    result = host.run(DecisionFrameContext(f"f{index}", index, 2000 + index, sensor_frame=sensors))
                    expected.append(result.context.control_applications)
            manifest = json.loads((recording.root / RECORDING_MANIFEST_NAME).read_text())
            contexts = [entry["context"] for entry in manifest["frames"]]
            self.assertEqual([len(item["control_applications"]) for item in contexts], [1, 3, 0, 1])
            self.assertEqual(contexts[1]["control_applications"][-1], contexts[1]["control_applications"][-2])
            self.assertEqual([item[CONTROL_APPLICATION_RESET_KEY] for item in contexts], [True, False, True, True])
            self.assertEqual([item.delivered_at_ms for item in expected[1]], [2000, 2000, 1000, 1000])
            exported = root / "export"
            batch = recording.read(run_id=recording.run_id, after=0)
            for item in batch["frames"]:
                frame = item["frame"]
                saved = recording.root / "perception" / frame["frame_id"] / "perception.json"
                self.assertNotIn('"control_applications"', saved.read_text())
                write_recorded_frame(exported, frame, base64.b64decode(item["image_base64"]),
                                     item["image_extension"], {})
            self.assertEqual(json.loads((exported / RECORDING_MANIFEST_NAME).read_text()), manifest)
            for directory in (recording.root, exported):
                source = load_image_source(directory)
                seen = []
                steps = {step: None for step in ("perception", "observation", "memory", "proposal", "plan", "action")}
                steps["memory"] = lambda context, observation: seen.append(context.control_applications)
                # The source reconstructs windows independently of replay order.
                for index in (3, 1, 0, 2):
                    run_frame(source.frames[index], steps=steps, shared_memory={})
                self.assertEqual(seen, [expected[index] for index in (3, 1, 0, 2)])

    def test_source_bounds_the_reconstructed_window_in_record_order(self):
        application = ControlApplication(None, "autonomy", True, "idle", AutonomyControl(), 100)
        with image_source(1) as root:
            frames = [{"frame_id": f"f{index}", "timestamp_ms": index, "image_path": "frame_00.png",
                       "context": {"control_applications": [replace(application, delivered_at_ms=100 - index).to_dict()]}}
                      for index in range(CONTROL_APPLICATION_LIMIT + 3)]
            write_manifest(root, {"frames": frames})
            source = load_image_source(root)
        self.assertEqual(len(source.frames[-1].control_applications), CONTROL_APPLICATION_LIMIT)
        self.assertEqual(source.frames[-1].control_applications[0].delivered_at_ms, 97)
        self.assertEqual(source.frames[-1].control_applications[-1].delivered_at_ms, 66)

    def test_malformed_applications_are_rejected_when_the_source_loads(self):
        valid = ControlApplication(None, "autonomy", True, "idle", AutonomyControl(), 100).to_dict()
        invalid = [None, "application", {}, {**valid, "mode": "local"},
                   {**valid, "applied": "true"}, {**valid, "delivered_at_ms": -1},
                   {**valid, "delivered_at_ms": None}, {**valid, "control": {}},
                   {**valid, "control": {**valid["control"], "steering": "left"}},
                   {**valid, "control": {**valid["control"], "throttle": float("nan")}},
                   {**valid, "receipt": []}]
        with image_source(1) as root:
            for application in invalid:
                with self.subTest(application=application):
                    write_manifest(root, {"frames": [{"image_path": "frame_00.png", "context": {
                        "control_applications": [application],
                    }}]})
                    with self.assertRaisesRegex(SourceValidationError, "invalid recorded context"):
                        load_image_source(root)
            for context in ({"control_applications": {}}, {"control_applications": None},
                            {CONTROL_APPLICATION_RESET_KEY: "true"}):
                write_manifest(root, {"frames": [{"image_path": "frame_00.png", "context": context}]})
                with self.assertRaises(SourceValidationError):
                    load_image_source(root)
