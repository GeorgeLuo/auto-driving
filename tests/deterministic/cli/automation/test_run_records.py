"""Run records require the complete shape and identify malformed persisted fields."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autonomy.fields import FieldError, MAX_SAFE_INT
from cli.automa_cli.hosts.runs.models import RunState, WorkerProcess
from cli.automa_cli.hosts.runs.store import mark_automation_startup_error, read_process_record, read_run_state


class RunRecordTests(unittest.TestCase):
    def test_only_missing_files_have_no_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, read in (("state.json", read_run_state), ("process.json", read_process_record)):
                self.assertIsNone(read(root))
                for text in ("{", "null", "[]", '"record"', "42"):
                    with self.subTest(name=name, text=text):
                        (root / name).write_text(text)
                        with self.assertRaises(FieldError) as caught:
                            read(root)
                        self.assertEqual(caught.exception.field_name, "$")

    def test_older_partial_records_are_rejected_with_the_missing_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, read, record, later in (
                ("state.json", read_run_state, RunState(), "completed_at_ms"),
                ("process.json", read_process_record, WorkerProcess(), "startup"),
            ):
                document = record.to_dict()
                del document[later]
                (root / name).write_text(json.dumps(document))
                with self.subTest(name=name), self.assertRaises(FieldError) as caught:
                    read(root)
                self.assertEqual(caught.exception.field_name, later)

    def test_invalid_counts_and_identifiers_are_rejected(self):
        for record, field, value in (
            (RunState(), "frames_received", "2"), (RunState(), "pid", True),
            (RunState(), "processed_count", -1), (WorkerProcess(), "pid", MAX_SAFE_INT + 1),
            (RunState(), "host_name", " car "), (WorkerProcess(), "command", ["python", 3]),
        ):
            document = record.to_dict()
            document[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(FieldError) as caught:
                type(record).from_dict(document)
            self.assertTrue(caught.exception.field_name.startswith(field))

    def test_startup_failure_rewrite_records_the_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = RunState(host_name="car", status="starting")
            (root / "state.json").write_text(json.dumps(state.to_dict()))
            with patch("cli.automa_cli.hosts.runs.models.timestamp_ms", return_value=123):
                mark_automation_startup_error(automation_dir=root, error="launch failed")
            written = read_run_state(root)
            self.assertEqual((written.host_name, written.status, written.error, written.completed_at_ms),
                             ("car", "error", "launch failed", 123))
            self.assertIsNone(written.session)

    def test_later_fields_are_always_written(self):
        process = WorkerProcess(pid=123)
        self.assertIsNone(process.to_dict()["stopped_by"])
        self.assertIsNone(process.to_dict()["log_path"])
        process.status, process.stopped_by, process.stopped_at_ms = "stopped", "stop_command", 123
        self.assertEqual({key: process.to_dict()[key] for key in ("status", "stopped_by", "stopped_at_ms")},
                         {"status": "stopped", "stopped_by": "stop_command", "stopped_at_ms": 123})
        state = RunState(host_name="car")
        self.assertEqual(state.to_dict()["last_frame"], {})
        self.assertIsNone(state.to_dict()["session"])
        state.last_frame = {"frame_id": "f0"}
        self.assertEqual(state.to_dict()["last_frame"], {"frame_id": "f0"})
