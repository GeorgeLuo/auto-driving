"""A local host's recording and the CLI export must have independent writers."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from autonomy.decision_cycle.activation import STEPS, step_activation_path, write_step_activation
from autonomy.runtime.assembly import staged_runtime
from autonomy.runtime import recording as recording_module
from autonomy.runtime.recording import RunRecording
from autonomy.runtime.session import RunConfiguration
from cli.automa_cli.runtime_monitor import _sync_recording
from implementations.decision_cycle.catalog import packaged_activation
from implementations.runtime.picar import create_host
from tests.shared.cli.workbench_fixtures import image_source
from tests.shared.integration.plugin_flow_fixtures import frame

VEHICLE_ID = "chase-sim-chaser"


class RecordingTransferTests(unittest.TestCase):
    def test_host_append_during_cli_export_keeps_both_histories_complete(self):
        with image_source(1) as root:
            runtime_root = root / "runtime"
            for step in STEPS:
                activation = packaged_activation(step, ["frame"] if step == "perception" else None)
                write_step_activation(step_activation_path(runtime_root, step), activation)
            host, options = staged_runtime(runtime_root, lambda steps: create_host(steps=steps))
            self.addCleanup(host.close)
            recording = RunRecording(options["recording_root"], vehicle_id=VEHICLE_ID)
            host.start(RunConfiguration(mode="observe_only", num_decisions=2), recording=recording)
            host.run(frame(root, 1))
            export_dir = runtime_root / "automation" / "runs" / recording.run_id
            state = {"recorded_count": 0, "vehicle_id": VEHICLE_ID}

            class Client:
                def read_recording(self, run_id, *, after):
                    return recording.read(run_id=run_id, after=after)

            original_write = recording_module.write_json_atomically
            appended = False

            def write_with_host_append(path: Path, payload: dict):
                nonlocal appended
                if path == export_dir / "manifest.json" and not appended:
                    # The CLI has read the old manifest. A host append commits
                    # before that CLI write, forcing the former lost-update race.
                    appended = True
                    host.run(frame(root, 2))
                original_write(path, payload)

            with patch.object(recording_module, "write_json_atomically", side_effect=write_with_host_append):
                for _ in range(2):
                    _sync_recording(Client(), state, run_dir=export_dir, recording_id=recording.run_id)

            self.assertTrue(appended)
            self.assertEqual(state["recorded_count"], 2)
            self.assertEqual(recording.status()["recorded_count"], 2)
            authoritative = json.loads((recording.root / "manifest.json").read_text())
            exported = json.loads((export_dir / "manifest.json").read_text())
            self.assertEqual([item["frame_id"] for item in authoritative["frames"]], ["frame-1", "frame-2"])
            self.assertEqual(exported, authoritative)
            self.assertEqual(host.session_status()["status"], "completed")
            self.assertNotEqual(recording.root.resolve(), export_dir.resolve())
            for entry in exported["frames"]:
                self.assertEqual((export_dir / entry["image_path"]).read_bytes(),
                                 (recording.root / entry["image_path"]).read_bytes())
                record = Path("perception") / entry["frame_id"] / "perception.json"
                self.assertEqual((export_dir / record).read_bytes(), (recording.root / record).read_bytes())


if __name__ == "__main__":
    unittest.main()
