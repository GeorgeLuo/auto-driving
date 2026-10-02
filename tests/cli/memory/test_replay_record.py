from __future__ import annotations
import json
import tempfile
import unittest
from pathlib import Path
from cli.automa_cli.memory import (
    _directory_byte_size,
    load_memory_observation_sequence,
    write_memory_replay_record,
)
from tests.cli.memory.replay_fixtures import (
    RECURRENCE_SOURCE,
    MemoryReplayFixture,
)


class MemoryReplayTests(MemoryReplayFixture, unittest.TestCase):

    def test_record_stabilizes_bytes_in_record_on_disk(self) -> None:
        frames = load_memory_observation_sequence(RECURRENCE_SOURCE)
        payload = {
            "schema": "vehicle_memory_replay_v0",
            "vehicle_id": "chase-sim-chaser",
            "frame_count": len(frames),
            "plugin_ids": ["bounded_evidence"],
            "digest": "abc",
            "final": {
                "health": "healthy",
                "record_count": 1,
                "records": [
                    {
                        "record_id": "thing:1:11:floor_plane:18:floor_boundary_000",
                        "kind": "floor_boundary",
                        "label": "boundary",
                        "confidence": 0.9,
                        "origin": {
                            "frame_id": "frame_001",
                            "observation_id": "obs_001",
                            "observed_id": "floor_boundary_000",
                        },
                    }
                ],
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp) / "memory-replay"
            written = write_memory_replay_record(
                vehicle_id="chase-sim-chaser",
                sequence_path=RECURRENCE_SOURCE,
                frames=frames,
                payload=payload,
                output_root=output_root,
                max_frames=16,
                max_record_bytes=2 * 1024 * 1024,
            )
            record_dir = output_root / Path(written["record_dir"]).name
            on_disk = _directory_byte_size(record_dir)
            manifest = json.loads(
                (record_dir / "manifest.json").read_text(encoding="utf-8")
            )
            result_on_disk = json.loads(
                (record_dir / "result.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["bounds"]["bytes_in_record"], on_disk)
            self.assertEqual(
                result_on_disk["record_manifest"]["bounds"]["bytes_in_record"],
                on_disk,
            )
            self.assertEqual(
                result_on_disk["record_bounds"]["bytes_in_record"], on_disk
            )
