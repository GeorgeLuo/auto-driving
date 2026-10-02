from __future__ import annotations
import json
import tempfile
import unittest
from pathlib import Path
from cli.automa_cli.memory import (
    MEMORY_REPLAY_MAX_FRAMES,
    load_memory_observation_sequence,
    replay_vehicle_memory,
)
from tests.cli.memory.replay_fixtures import (
    MemoryReplayFixture,
)


class MemoryReplayTests(MemoryReplayFixture, unittest.TestCase):
    def test_replay_rejects_sequences_over_frame_ceiling(self) -> None:
        frames = [
            {
                "frame_id": f"frame_{index:04d}",
                "frame_index": index,
                "timestamp_ms": 1_000 + index,
                "observation": {
                    "observation_id": f"obs_{index:04d}",
                    "created_at_ms": 1_000 + index,
                    "sensor_frame": {},
                    "perception_plugin_id": "lightweight_observer",
                    "summary": [f"frame {index}"],
                    "things": [],
                    "signals": [],
                },
            }
            for index in range(MEMORY_REPLAY_MAX_FRAMES + 1)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            sequence = Path(tmp) / "too_long.json"
            sequence.write_text(
                json.dumps(
                    {
                        "schema": "automa_memory_observation_sequence_v0",
                        "frames": frames,
                    }
                ),
                encoding="utf-8",
            )
            result = replay_vehicle_memory(
                vehicle_id="chase-sim-chaser",
                sequence=sequence,
                plugin_id="bounded_evidence",
                json_output=True,
            )
        self.assertEqual(result.exit_code, 2)
        self.assertIn("max allowed", result.message)


    def test_loader_directory_accepts_exact_frame_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index in range(3):
                (root / f"frame_{index:03d}.json").write_text(
                    json.dumps(
                        {
                            "frame_id": f"frame_{index:03d}",
                            "frame_index": index,
                            "timestamp_ms": index,
                            "observation": {
                                "observation_id": f"obs_{index:03d}",
                                "created_at_ms": index,
                                "sensor_frame": {},
                                "perception_plugin_id": "lightweight_observer",
                                "summary": [],
                                "things": [],
                                "signals": [],
                            },
                        }
                    ),
                    encoding="utf-8",
                )
            frames = load_memory_observation_sequence(root, max_frames=3)
            self.assertEqual(len(frames), 3)

    def test_loader_file_accepts_exact_frame_boundary(self) -> None:
        frames = [
            {
                "frame_id": f"frame_{index:04d}",
                "frame_index": index,
                "timestamp_ms": 1_000 + index,
                "observation": {
                    "observation_id": f"obs_{index:04d}",
                    "created_at_ms": 1_000 + index,
                    "sensor_frame": {},
                    "perception_plugin_id": "lightweight_observer",
                    "summary": [f"frame {index}"],
                    "things": [],
                    "signals": [],
                },
            }
            for index in range(3)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            sequence = Path(tmp) / "exact.json"
            sequence.write_text(
                json.dumps(
                    {
                        "schema": "automa_memory_observation_sequence_v0",
                        "frames": frames,
                    }
                ),
                encoding="utf-8",
            )
            loaded = load_memory_observation_sequence(sequence, max_frames=3)
            self.assertEqual(len(loaded), 3)

