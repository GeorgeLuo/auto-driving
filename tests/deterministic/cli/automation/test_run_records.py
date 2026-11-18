"""Run records require the complete shape and identify malformed persisted fields."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autonomy.fields import FieldError, MAX_SAFE_INT
from cli.automa_cli.hosts.runs.models import RunState, WorkerProcess
from cli.automa_cli.hosts.runs.store import (
    RunRecordError, convert_legacy_run_record, mark_automation_startup_error, read_process_record, read_run_state,
)


class RunRecordTests(unittest.TestCase):
    def test_only_missing_files_have_no_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, read in (("state.json", read_run_state), ("process.json", read_process_record)):
                self.assertIsNone(read(root))
                for text in ("{", "null", "[]", '"record"', "42"):
                    with self.subTest(name=name, text=text):
                        (root / name).write_text(text)
                        with self.assertRaises(RunRecordError) as caught:
                            read(root)
                        self.assertEqual(caught.exception.__cause__.field_name, "$")

    def test_main_5f0cf136_records_load_and_round_trip(self):
        # Literal outputs of that build's to_dict, not documents from this branch's writer.
        fresh_state = {
            "schema": None, "host_name": None, "run_id": None, "status": None, "pid": None,
            "started_at_ms": None, "updated_at_ms": None, "frames_received": 0, "frames_dropped": None,
            "processed_count": 0, "num_decisions": None, "pipeline": None, "control_source": None,
            "action_policy": None, "control_application": None, "recording": None, "recorded_count": 0,
            "run_dir": None,
        }
        fresh_process = {
            "schema": None, "host_name": None, "pid": None, "started_at_ms": None, "command": None,
            "log_to_disk": False, "log_path": None, "state_path": None, "stream_command": None,
        }
        running_state = {
            "schema": "automa_automation_run_state", "host_name": "car", "run_id": "run-1",
            "status": "running", "pid": 123, "started_at_ms": 1000, "updated_at_ms": 1100,
            "frames_received": 2, "frames_dropped": 0, "processed_count": 1, "num_decisions": 3,
            "pipeline": "latest_frame_async_decision", "control_source": "runtime_host",
            "action_policy": "observe_only", "control_application": "not_applied", "recording": False,
            "recorded_count": 0, "run_dir": None,
        }
        running_process = {
            "schema": "automa_automation_process", "host_name": "car", "pid": 123, "started_at_ms": 1000,
            "command": ["python", "-m", "cli.automa_cli.hosts.runs.worker"], "log_to_disk": False,
            "log_path": None, "state_path": "runtime/state.json", "stream_command": "automa hosts stream",
        }
        for name, read, cls, payload in (
            ("state.json", read_run_state, RunState, fresh_state),
            ("process.json", read_process_record, WorkerProcess, fresh_process),
            ("state.json", read_run_state, RunState, running_state),
            ("process.json", read_process_record, WorkerProcess, running_process),
        ):
            with self.subTest(name=name, payload=payload), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                path = root / name
                path.write_text(json.dumps(payload))
                record = read(root)
                self.assertEqual(record, cls(**payload))
                converted = convert_legacy_run_record(payload, record_name=name)
                self.assertEqual(record.to_dict(), converted)
                self.assertEqual(cls.from_dict(converted), record)
                path.write_text(json.dumps(record.to_dict()))
                self.assertEqual(read(root), record)

    def test_conversion_preserves_present_later_fields_and_has_no_shared_defaults(self):
        state = RunState(
            completed_at_ms=123, session={}, execution={"mode": "manual"},
            selections={"proposal": {"plugins": ["fixture"]}}, error="failed",
        )
        process = WorkerProcess(startup={}, status="stopped", stopped_at_ms=123)
        for name, record in (("state.json", state), ("process.json", process)):
            document = record.to_dict()
            self.assertEqual(convert_legacy_run_record(document, record_name=name), document)
        first = convert_legacy_run_record({}, record_name="state.json")
        first["selections"]["proposal"] = {}
        self.assertEqual(convert_legacy_run_record({}, record_name="state.json")["selections"], {})

    def test_only_the_listed_absent_fields_are_converted(self):
        for name, read, record in (
            ("state.json", read_run_state, RunState()), ("process.json", read_process_record, WorkerProcess()),
        ):
            for change, field in (("missing", "pid"), ("extra", "unexpected"), ("invalid", "started_at_ms")):
                document = record.to_dict()
                if change == "missing":
                    del document[field]
                else:
                    document[field] = -1
                with self.subTest(name=name, change=change), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    (root / name).write_text(json.dumps(document))
                    with self.assertRaises(RunRecordError) as caught:
                        read(root)
                    self.assertEqual(caught.exception.__cause__.field_name, field)
        # A present invalid later field is never replaced by its default.
        for name, read, record, field in (
            ("state.json", read_run_state, RunState(), "session"),
            ("process.json", read_process_record, WorkerProcess(), "startup"),
        ):
            document = record.to_dict()
            document[field] = []
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / name).write_text(json.dumps(document))
                with self.assertRaises(RunRecordError) as caught:
                    read(root)
                self.assertEqual(caught.exception.__cause__.field_name, field)

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
