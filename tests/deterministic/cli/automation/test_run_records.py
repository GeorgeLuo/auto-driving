"""Run records tolerate old files and preserve partial-record omissions."""

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

    def test_partial_startup_failure_rewrite_keeps_nulls_and_extra_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = {"host_name": "car", "pid": None, "status": "starting", "extra": {"v": 1}}
            (root / "state.json").write_text(json.dumps(original))
            with patch("cli.automa_cli.hosts.runs.models.timestamp_ms", return_value=123):
                mark_automation_startup_error(automation_dir=root, error="launch failed")
            self.assertEqual(json.loads((root / "state.json").read_text()),
                             {**original, "status": "error", "error": "launch failed", "exit_code": None,
                              "completed_at_ms": 123, "updated_at_ms": 123})
            state = read_run_state(root)
            self.assertNotIn("session", state.to_dict())
            state.session, state.arming, state.decision = None, None, {}
            self.assertEqual({key: state.to_dict()[key] for key in ("session", "arming", "decision")},
                             {"session": None, "arming": None, "decision": {}})

    def test_old_process_record_keeps_omissions_when_stopped(self):
        original = {"pid": 123, "log_path": None, "startup": {"status": "failed"}}
        process = WorkerProcess.from_dict(original)
        self.assertEqual(process.to_dict(), original)
        process.status, process.stopped_by, process.stopped_at_ms = "stopped", "stop_command", 123
        self.assertEqual(process.to_dict(), {**original, "status": "stopped", "stopped_by": "stop_command",
                                             "stopped_at_ms": 123})
