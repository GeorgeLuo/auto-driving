"""The host publishes its applications in shared memory; recordings store them once and replay restores them."""
from __future__ import annotations

import base64
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from autonomy.decision_cycle.action.publication import CONTROL_APPLICATIONS_KEY
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.cycle import DecisionSteps
from autonomy.runtime.cycle_host import AutonomyCycleHost
from autonomy.runtime.control import AutonomyControl
from autonomy.runtime.execution import CONTROL_APPLICATION_LIMIT, ControlApplication
from autonomy.runtime.recording import (
    CONTROL_APPLICATION_CHANGE_FIELD, RECORDING_MANIFEST_NAME, RunRecording,
    control_application_windows,
    write_recorded_frame,
)
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorFrame, SensorReading
from cli.automa_cli.workbench_frames import run_frame
from cli.automa_cli.workbench_source import SourceValidationError, load_image_source
from implementations.runtime.picar.control import DonkeyControlTarget
from tests.shared.cli.workbench_fixtures import image_source, write_manifest


class ControlApplicationRecordingTests(unittest.TestCase):
    def test_recording_and_export_store_changes_once_and_replay_windows(self):
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
                    result = host.run(DecisionFrameContext(f"f{index}", index, 2100 + index, sensor_frame=sensors),
                                      mode="local", user_steering=index / 10, user_throttle=0.4)
                    expected.append(result.context.shared_memory[CONTROL_APPLICATIONS_KEY])
            manifest = json.loads((recording.root / RECORDING_MANIFEST_NAME).read_text())
            entries = manifest["frames"]
            self.assertEqual([entry["mode"] for entry in entries], ["autonomy"] * 4)
            self.assertEqual([entry["user_steering"] for entry in entries], [0, 0.1, 0.2, 0.3])
            self.assertTrue(all(entry["user_throttle"] == 0.4 for entry in entries))
            self.assertTrue(all(set(entry["context"]) == {"timestamp_ms"} for entry in entries))
            changes = [entry[CONTROL_APPLICATION_CHANGE_FIELD] for entry in entries]
            self.assertEqual([len(change["added"]) for change in changes], [1, 3, 1, 1])
            self.assertEqual(changes[1]["added"][-1], changes[1]["added"][-2])
            self.assertEqual([change["kept"] for change in changes], [0, 1, 4, 5])
            self.assertTrue(all(CONTROL_APPLICATION_CHANGE_FIELD not in entry["context"] for entry in entries))
            self.assertEqual([item.delivered_at_ms for item in expected[1]], [2000, 2000, 1000, 1000])
            exported = root / "export"
            batch = recording.read(run_id=recording.run_id, after=0)
            for item in batch["frames"]:
                frame = item["frame"]
                saved = recording.root / "perception" / frame["frame_id"] / "perception.json"
                self.assertNotIn(CONTROL_APPLICATION_CHANGE_FIELD, saved.read_text())
                write_recorded_frame(exported, frame, base64.b64decode(item["image_base64"]),
                                     item["image_extension"], {})
            self.assertEqual(json.loads((exported / RECORDING_MANIFEST_NAME).read_text()), manifest)
            for directory in (recording.root, exported):
                source = load_image_source(directory)
                seen = []
                cycle_times = []
                capture_times = []
                steps = {step: None for step in ("perception", "observation", "memory", "proposal", "plan", "action")}

                def remember(context, observation):
                    seen.append(context.shared_memory[CONTROL_APPLICATIONS_KEY])
                    self.assertEqual(context.shared_memory["action.mode"], "autonomy")
                    self.assertEqual(context.shared_memory["action.user_steering"], context.frame_index / 10)
                    self.assertEqual(context.shared_memory["action.user_throttle"], 0.4)
                    cycle_times.append(context.timestamp_ms)
                    capture_times.append(context.sensor_frame.readings[FRONT_CAMERA_SENSOR_ID].captured_at_ms)

                steps["memory"] = remember
                # The source reconstructs windows independently of replay order.
                for index in (3, 1, 0, 2):
                    run_frame(source.frames[index], steps=steps, shared_memory={})
                self.assertEqual(seen, [expected[index] for index in (3, 1, 0, 2)])
                self.assertEqual(cycle_times, [2103, 2101, 2100, 2102])
                self.assertEqual(capture_times, [2003, 2001, 2000, 2002])

    def test_recording_encodes_partial_overlap_no_overlap_and_unchanged_windows(self):
        records = tuple(ControlApplication(None, "autonomy", True, "idle", AutonomyControl(), 100)
                        for _ in range(5))
        windows = [records[:3], records[1:4], records[1:4], records[4:], None, (), records[:1]]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            camera = root / "camera.png"
            Image.new("RGB", (2, 2)).save(camera)
            host = AutonomyCycleHost(steps=DecisionSteps())
            self.addCleanup(host.close)
            recording = RunRecording(root, vehicle_id="picar", run_id="windows")
            for index, window in enumerate(windows):
                sensors = SensorFrame(
                    read_id=f"f{index}", readings={FRONT_CAMERA_SENSOR_ID: SensorReading(
                        sensor_id=FRONT_CAMERA_SENSOR_ID, sensor_kind="camera",
                        captured_at_ms=index, path=str(camera),
                    )}, started_at_ms=index, completed_at_ms=index,
                )
                result = host.run(DecisionFrameContext(f"f{index}", index, index, sensor_frame=sensors))
                recording.append(result, window)
            entries = json.loads((recording.root / RECORDING_MANIFEST_NAME).read_text())["frames"]
            changes = [entry.get(CONTROL_APPLICATION_CHANGE_FIELD) for entry in entries]
            self.assertEqual([change["kept"] if change else None for change in changes], [0, 2, 3, 0, None, 0, 0])
            self.assertEqual([len(change["added"]) if change else None for change in changes], [3, 1, 0, 1, None, 0, 1])
            self.assertEqual(control_application_windows(changes), windows)
            source = load_image_source(recording.root)
            self.assertEqual([frame.control_applications for frame in source.frames], windows)

    def test_source_uses_kept_in_record_order_without_a_runtime_limit(self):
        application = ControlApplication(None, "autonomy", True, "idle", AutonomyControl(), 100)
        count = CONTROL_APPLICATION_LIMIT + 3
        with image_source(1) as root:
            frames = [{"frame_id": f"f{index}", "timestamp_ms": index, "image_path": "frame_00.png",
                       CONTROL_APPLICATION_CHANGE_FIELD: {
                           "kept": index,
                           "added": [replace(application, delivered_at_ms=100 - index).to_dict()],
                       }} for index in range(count)]
            frames.append({"frame_id": "last", "timestamp_ms": count, "image_path": "frame_00.png",
                           CONTROL_APPLICATION_CHANGE_FIELD: {"kept": 2, "added": []}})
            write_manifest(root, {"frames": frames})
            source = load_image_source(root)
        self.assertEqual(len(source.frames[-2].control_applications), count)
        self.assertEqual(source.frames[-1].control_applications, source.frames[-2].control_applications[-2:])
        self.assertEqual(source.frames[-1].control_applications[-1].delivered_at_ms, 100 - count + 1)

    def test_missing_windows_break_history_and_zero_kept_discards_it(self):
        application = ControlApplication(None, "autonomy", True, "idle", AutonomyControl(), 100)
        initial = {"kept": 0, "added": [application.to_dict()]}
        self.assertEqual(control_application_windows([
            initial, {"kept": 0, "added": []}, initial, None, initial,
        ]), [(application,), (), (application,), None, (application,)])
        with self.assertRaisesRegex(ValueError, "frame 2"):
            control_application_windows([initial, None, {"kept": 1, "added": []}])

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
                    write_manifest(root, {"frames": [{
                        "image_path": "frame_00.png",
                        CONTROL_APPLICATION_CHANGE_FIELD: {"kept": 0, "added": [application]},
                    }]})
                    with self.assertRaisesRegex(SourceValidationError, "invalid control applications"):
                        load_image_source(root)
            for change in ({}, [], {"kept": 0}, {"kept": 0, "added": {}},
                           {"kept": True, "added": []}, {"kept": -1, "added": []},
                           {"kept": 1, "added": []}, {"kept": 0.5, "added": []}):
                with self.subTest(change=change):
                    write_manifest(root, {"frames": [{"image_path": "frame_00.png",
                                                      CONTROL_APPLICATION_CHANGE_FIELD: change}]})
                    with self.assertRaisesRegex(SourceValidationError, "frame 0 has invalid control applications"):
                        load_image_source(root)

    def test_replay_removes_the_key_when_the_source_recorded_none(self):
        application = ControlApplication(None, "autonomy", True, "idle", AutonomyControl(), 100)
        with image_source(1) as root:
            write_manifest(root, {"frames": [{"image_path": "frame_00.png"}]})
            source = load_image_source(root)
        self.assertIsNone(source.frames[0].control_applications)
        shared_memory = {CONTROL_APPLICATIONS_KEY: (application,)}
        seen = []
        steps = {step: None for step in ("perception", "observation", "memory", "proposal", "plan", "action")}
        steps["memory"] = lambda context, observation: seen.append(dict(context.shared_memory))
        run_frame(source.frames[0], steps=steps, shared_memory=shared_memory)
        self.assertNotIn(CONTROL_APPLICATIONS_KEY, seen[0])
        self.assertIsNone(seen[0]["action.mode"])
