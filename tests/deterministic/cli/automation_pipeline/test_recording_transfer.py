"""A local host's recording and the CLI export must have independent writers."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from autonomy.decision_cycle.activation import STEPS, step_activation_path, write_step_activation
from autonomy.runtime.assembly import staged_runtime
from autonomy.runtime import recording as recording_module
from autonomy.runtime.recording import RECORDING_ENTRIES_NAME, RunRecording, host_runs_dir, read_recording_entries
from autonomy.runtime.session import RunConfiguration
from cli.automa_cli.hosts.runs.monitor import _sync_recording
from implementations.decision_cycle.catalog import packaged_activation
from implementations.runtime.picar import create_host
from tests.shared.cli.workbench_fixtures import image_source
from tests.shared.integration.plugin_flow_fixtures import frame

HOST_NAME = "chase-sim-chaser"


class RecordingTransferTests(unittest.TestCase):
    def test_host_append_during_cli_export_keeps_both_histories_complete(self):
        with image_source(1) as root:
            runtime_root = root / "runtime"
            for step in STEPS:
                activation = packaged_activation(step, ["frame"] if step == "perception" else None)
                write_step_activation(step_activation_path(runtime_root, step), activation)
            host = staged_runtime(runtime_root, lambda steps: create_host(steps=steps))
            self.addCleanup(host.close)
            recording = RunRecording(host_runs_dir(runtime_root), host_name=HOST_NAME)
            host.start(RunConfiguration(mode="observe_only", num_decisions=2), recording=recording)
            first = host.run(frame(root, 1))
            timings = {first.context.frame_id: first.timing}
            completions = {first.context.frame_id: first.completed_at_ms}
            export_dir = runtime_root / "automation" / "runs" / recording.run_id
            state = {"recorded_count": 0, "host_name": HOST_NAME}

            class Client:
                def read_recording(self, run_id, *, after):
                    return recording.read(run_id=run_id, after=after)

            original_append = recording_module.append_recording_entry
            appended = False

            def append_with_host_frame(path: Path, entry: dict):
                nonlocal appended
                if path == export_dir / RECORDING_ENTRIES_NAME and not appended:
                    # The CLI is committing this frame's entry. A host append
                    # commits before that write returns.
                    appended = True
                    second = host.run(frame(root, 2))
                    timings[second.context.frame_id] = second.timing
                    completions[second.context.frame_id] = second.completed_at_ms
                original_append(path, entry)

            with patch.object(recording_module, "append_recording_entry", side_effect=append_with_host_frame):
                for _ in range(2):
                    _sync_recording(Client(), state, run_dir=export_dir, recording_id=recording.run_id)

            self.assertTrue(appended)
            self.assertEqual(state["recorded_count"], 2)
            self.assertEqual(recording.status()["recorded_count"], 2)
            authoritative = read_recording_entries(recording.root)
            exported = read_recording_entries(export_dir)
            self.assertEqual([item["frame_id"] for item in authoritative], ["frame-1", "frame-2"])
            self.assertEqual(exported, authoritative)
            self.assertEqual(
                (export_dir / "manifest.json").read_text(),
                (recording.root / "manifest.json").read_text(),
            )
            self.assertEqual(host.session_status()["status"], "completed")
            self.assertNotEqual(recording.root.resolve(), export_dir.resolve())
            for entry in exported:
                self.assertEqual((export_dir / entry["image_path"]).read_bytes(),
                                 (recording.root / entry["image_path"]).read_bytes())
                record = Path("perception") / entry["frame_id"] / "perception.json"
                self.assertEqual((export_dir / record).read_bytes(), (recording.root / record).read_bytes())
                saved = json.loads((recording.root / record).read_text())
                self.assertEqual(saved["timing"], timings[entry["frame_id"]])
                self.assertEqual(saved["completed_at_ms"], completions[entry["frame_id"]])
                for alias in ("sensor_frame", "cycle_duration_ms", "perception_duration_ms", "perception_completed_at_ms"):
                    self.assertNotIn(alias, saved)
                self.assertTrue(any(step["plugins"] for step in saved["timing"]["steps"]))


if __name__ == "__main__":
    unittest.main()
