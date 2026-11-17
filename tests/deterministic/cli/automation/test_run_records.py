"""Run records tolerate old files and leave out fields a run has not set."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cli.automa_cli.hosts.runs.models import RunState, WorkerProcess
from cli.automa_cli.hosts.runs.store import mark_automation_startup_error, read_process_record, read_run_state


class RunRecordTests(unittest.TestCase):
    def test_missing_invalid_and_non_object_files_have_no_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, read in (("state.json", read_run_state), ("process.json", read_process_record)):
                self.assertIsNone(read(root))
                for text in ("{", "null", "[]", '"record"', "42"):
                    (root / name).write_text(text)
                    self.assertIsNone(read(root))

    def test_wrong_field_types_use_absent_defaults(self):
        state = RunState.from_dict({"pid": True, "status": [], "frames_received": "2", "session": [],
                                    "decision": False, "last_frame": 5, "recording": "false"})
        self.assertEqual((state.pid, state.status, state.frames_received, state.session), (None, None, 0, None))
        self.assertEqual((state.decision, state.last_frame, state.recording), ({}, {}, None))
        process = WorkerProcess.from_dict({"pid": "123", "log_path": [], "command": ["python", 3]})
        self.assertEqual((process.pid, process.log_path, process.command, process.log_to_disk),
                         (None, None, None, False))

    def test_partial_startup_failure_rewrite_records_the_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "state.json").write_text(json.dumps({"host_name": "car", "pid": None, "status": "starting"}))
            with patch("cli.automa_cli.hosts.runs.models.timestamp_ms", return_value=123):
                mark_automation_startup_error(automation_dir=root, error="launch failed")
            written = json.loads((root / "state.json").read_text())
            self.assertEqual({key: written[key] for key in ("host_name", "status", "error", "completed_at_ms")},
                             {"host_name": "car", "status": "error", "error": "launch failed", "completed_at_ms": 123})
            self.assertNotIn("session", written)

    def test_fields_a_run_sets_later_are_written_once_set(self):
        process = WorkerProcess.from_dict({"pid": 123, "log_path": None})
        self.assertNotIn("stopped_by", process.to_dict())
        self.assertIn("log_path", process.to_dict())
        process.status, process.stopped_by, process.stopped_at_ms = "stopped", "stop_command", 123
        self.assertEqual({key: process.to_dict()[key] for key in ("status", "stopped_by", "stopped_at_ms")},
                         {"status": "stopped", "stopped_by": "stop_command", "stopped_at_ms": 123})
        state = RunState(host_name="car")
        self.assertNotIn("last_frame", state.to_dict())
        state.last_frame = {"frame_id": "f0"}
        self.assertEqual(state.to_dict()["last_frame"], {"frame_id": "f0"})
