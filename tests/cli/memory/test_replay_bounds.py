from __future__ import annotations
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
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
                    "sensor_snapshot": {},
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
                implementation_id="bounded_evidence",
                json_output=True,
            )
        self.assertEqual(result.exit_code, 2)
        self.assertIn("max allowed", result.message)

    def test_loader_enforces_max_frames_before_return(self) -> None:
        frames = [
            {
                "frame_id": f"frame_{index:04d}",
                "frame_index": index,
                "timestamp_ms": 1_000 + index,
                "observation": {
                    "observation_id": f"obs_{index:04d}",
                    "created_at_ms": 1_000 + index,
                    "sensor_snapshot": {},
                    "perception_plugin_id": "lightweight_observer",
                    "summary": [f"frame {index}"],
                    "things": [],
                    "signals": [],
                },
            }
            for index in range(5)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            sequence = Path(tmp) / "five.json"
            sequence.write_text(
                json.dumps(
                    {
                        "schema": "automa_memory_observation_sequence_v0",
                        "frames": frames,
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "max allowed is 3"):
                load_memory_observation_sequence(sequence, max_frames=3)

    def test_loader_enforces_sequence_file_byte_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sequence = Path(tmp) / "bulky.json"
            sequence.write_text("{" + ("x" * 200) + "}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "max allowed is 64"):
                load_memory_observation_sequence(
                    sequence,
                    max_frames=16,
                    max_sequence_file_bytes=64,
                )

    def test_loader_directory_rejects_excess_frame_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index in range(4):
                (root / f"frame_{index:03d}.json").write_text(
                    json.dumps(
                        {
                            "frame_id": f"frame_{index:03d}",
                            "frame_index": index,
                            "timestamp_ms": index,
                            "observation": {
                                "observation_id": f"obs_{index:03d}",
                                "created_at_ms": index,
                                "sensor_snapshot": {},
                                "perception_plugin_id": "lightweight_observer",
                                "summary": [],
                                "things": [],
                                "signals": [],
                            },
                        }
                    ),
                    encoding="utf-8",
                )
            with self.assertRaisesRegex(ValueError, "frame files"):
                load_memory_observation_sequence(root, max_frames=3)

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
                                "sensor_snapshot": {},
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
                    "sensor_snapshot": {},
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

    def test_loader_directory_stops_scan_at_max_plus_one(self) -> None:
        """Reject without materializing every matching path under a large directory."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index in range(12):
                (root / f"frame_{index:03d}.json").write_text(
                    json.dumps(
                        {
                            "frame_id": f"frame_{index:03d}",
                            "frame_index": index,
                            "timestamp_ms": index,
                            "observation": {
                                "observation_id": f"obs_{index:03d}",
                                "created_at_ms": index,
                                "sensor_snapshot": {},
                                "perception_plugin_id": "lightweight_observer",
                                "summary": [],
                                "things": [],
                                "signals": [],
                            },
                        }
                    ),
                    encoding="utf-8",
                )
            frame_hits: list[str] = []
            real_is_file = Path.is_file

            def tracking_is_file(self: Path) -> bool:
                result = real_is_file(self)
                # Count only real frame JSON matches (named sequence candidates may be probed first).
                if (
                    result
                    and self.parent == root
                    and self.name.startswith("frame_")
                    and self.suffix.lower() == ".json"
                ):
                    frame_hits.append(self.name)
                    if len(frame_hits) > 4:
                        raise AssertionError(
                            f"directory scan continued past max_frames+1: {frame_hits}"
                        )
                return result

            with patch.object(Path, "is_file", tracking_is_file):
                with self.assertRaisesRegex(ValueError, "more than 3 frame files"):
                    load_memory_observation_sequence(root, max_frames=3)
            # max_frames=3 → reject on the 4th match; no further frame is_file checks.
            self.assertEqual(len(frame_hits), 4)
            with patch.object(Path, "read_text", side_effect=AssertionError("read")):
                with self.assertRaisesRegex(ValueError, "more than 3 frame files"):
                    load_memory_observation_sequence(root, max_frames=3)
