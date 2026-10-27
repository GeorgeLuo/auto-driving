from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from implementations.decision_cycle.catalog import selection_activation
from tests.shared.cli.invoke import invoke_automa
from tests.shared.cli.memory.frame_fixtures import write_frames


# Where the frame was read from, and how long this run took on it.
# Plugin reports include measured durations; observations contain source image paths.
UNREPLAYED = ("image_path", "timing", "observation", "plugin_report")

class InspectionRecordingTests(unittest.TestCase):
    """Move a recording between the public perception and memory commands."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.frames = write_frames(self.root / "source")
        self.env = {
            "AUTOMA_PERCEPTION_INSPECT_ROOT": str(self.root / "perception"),
            "AUTOMA_MEMORY_INSPECT_ROOT": str(self.root / "memory"),
        }

    def inspect(self, step: str, source: Path, *options: str, exit_code: int = 0) -> dict:
        result = invoke_automa(
            "plugins",
            "inspect", step,
            str(source),
            *options,
            "--json",
            extra_env=self.env,
            check=False,
        )
        self.assertEqual(result.returncode, exit_code, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_equal_timestamp_perception_recording_replays_after_memory_recording_moves(
        self,
    ) -> None:
        for path in self.frames.iterdir():
            os.utime(path, (5, 5))
        (self.frames / "manifest.json").write_text(
            json.dumps(
                {
                    "frames": [
                        {"image_path": f"frame_{index}.png", "timestamp_ms": 5000}
                        for index in range(3)
                    ],
                }
            )
        )
        perception = self.inspect(
            "perception", self.frames, "--preset", "obstruction_observer", "--record"
        )
        memory = self.inspect("memory", Path(perception["run_dir"]), "--record")
        self.assertEqual(memory["selections"]["perception"], perception["selections"]["perception"])
        self.assertEqual(
            [frame["timestamp_ms"] for frame in memory["frames"]], [5000] * 3
        )
        records = memory["frames"][-1]["record"]["plugins"][0]["state"]["records"]
        self.assertTrue(any(record["kind"] == "obstacle" for record in records))

        moved = self.root / "moved"
        shutil.move(memory["run_dir"], moved)
        shutil.rmtree(self.frames)
        shutil.rmtree(self.root / "perception")
        for path in (moved / "frames").iterdir():
            os.utime(path, (99, 99))
        replay = self.inspect("memory", moved)
        self.assertEqual(replay["selections"]["perception"], memory["selections"]["perception"])
        self.assertEqual(replay["selections"]["memory"], memory["selections"]["memory"])
        self.assertEqual(
            [
                {key: value for key, value in frame.items() if key not in UNREPLAYED}
                for frame in replay["frames"]
            ],
            [
                {key: value for key, value in frame.items() if key not in UNREPLAYED}
                for frame in memory["frames"]
            ],
        )
        self.assertEqual(
            [Path(frame["image_path"]).parent for frame in replay["frames"]],
            [(moved / "frames").resolve()] * 3,
        )
        self.assertEqual(replay["frames"][-1]["record"], memory["frames"][-1]["record"])

        perceived = self.inspect("perception", moved)
        self.assertEqual(perceived["selections"]["perception"], perception["selections"]["perception"])
        self.assertEqual(
            [frame["timestamp_ms"] for frame in perceived["frames"]], [5000] * 3
        )
        self.assertEqual(
            [frame["frame_id"] for frame in perceived["frames"]],
            [frame["frame_id"] for frame in memory["frames"]],
        )

    def test_both_inspect_reports_name_the_perception_selection_alike(self) -> None:
        perception = invoke_automa(
            "plugins", "inspect", "perception", str(self.frames),
            "--preset", "obstruction_observer", "--record",
            extra_env=self.env,
        ).stdout.splitlines()
        recorded = Path(perception[-1].removeprefix("Recorded: "))
        memory = invoke_automa(
            "plugins", "inspect", "memory", str(recorded), extra_env=self.env
        ).stdout.splitlines()

        self.assertEqual((perception[0], memory[0]), ("Perception inspect", "Memory inspect"))
        self.assertTrue(perception[2].startswith("Source: "), perception)
        self.assertTrue(memory[2].startswith("Source: "), memory)
        self.assertTrue(perception[3].startswith("Steps: perception=frame,"), perception)
        self.assertEqual(memory[3], perception[3])
        self.assertEqual(recorded.parent, self.root / "perception")
        report = json.loads((recorded / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["schema"], "perception_inspect_v1")
        self.assertEqual(sorted(path.name for path in recorded.glob("*.json")), ["report.json"])

    def test_recording_preserves_dropout_frames_and_custom_memory_bounds(self) -> None:
        perception = selection_activation("perception", preset="multi_obstruction")
        memory = selection_activation("memory")
        memory_config = {
            "plugins": list(memory.plugins),
            "plugin_specs": dict(memory.plugin_specs),
            "plugin_configs": {
                "bounded_evidence": {"max_age_ms": 600, "max_records": 16}
            },
        }
        (self.frames / "report.json").write_text(
            json.dumps(
                {
                    "source_id": "recording.fixture",
                    "perception": {
                        "preset": "multi_obstruction",
                        "config": {
                            "plugins": list(perception.plugins),
                            "plugin_specs": dict(perception.plugin_specs),
                            "plugin_configs": dict(perception.plugin_configs),
                        },
                    },
                    "memory": {"preset": "custom", "config": memory_config},
                    "frames": [
                        {
                            "frame_id": "camera-a",
                            "frame_index": 7,
                            "timestamp_ms": 5000,
                            "image_path": "frame_0.png",
                        },
                        {
                            "frame_id": "dropout",
                            "frame_index": 9,
                            "timestamp_ms": 5000,
                            "absence_reason": "camera missing",
                        },
                        {
                            "frame_id": "camera-b",
                            "frame_index": 12,
                            "timestamp_ms": 5800,
                            "image_path": "frame_1.png",
                        },
                    ],
                }
            )
        )
        recorded = self.inspect("memory", self.frames, "--record")
        replay = self.inspect("memory", Path(recorded["run_dir"]))
        self.assertEqual(replay["selections"]["memory"]["config"], memory_config)
        self.assertEqual(
            [
                {key: value for key, value in frame.items() if key not in UNREPLAYED}
                for frame in replay["frames"]
            ],
            [
                {key: value for key, value in frame.items() if key not in UNREPLAYED}
                for frame in recorded["frames"]
            ],
        )
        self.assertEqual(replay["frames"][-1]["record"], recorded["frames"][-1]["record"])
        self.assertIsNone(replay["frames"][1]["record"])
        self.assertEqual(replay["frames"][1]["absence_reason"], "camera missing")
        self.assertIsNone(replay["frames"][1]["image_path"])
        self.assertEqual(
            replay["frames"][-1]["record"]["plugins"][0]["state"]["bounds"]["max_age_ms"], 600
        )
        overridden = self.inspect(
            "memory", Path(recorded["run_dir"]), "--preset", "recency_ledger"
        )
        self.assertEqual(overridden["selections"]["memory"]["preset"], "recency_ledger")
        self.assertEqual(
            overridden["frames"][-1]["record"]["plugins"][0]["state"]["bounds"]["max_age_ms"], 10000
        )

        perceived = self.inspect("perception", Path(recorded["run_dir"]), exit_code=0)
        self.assertEqual(
            [frame["frame_index"] for frame in perceived["frames"]], [7, 9, 12]
        )
        self.assertEqual(
            [frame["timestamp_ms"] for frame in perceived["frames"]], [5000, 5000, 5800]
        )

    def test_empty_recorded_selections_remain_empty_until_overridden(self) -> None:
        (self.frames / "report.json").write_text(
            json.dumps(
                {
                    "perception": {
                        "preset": "custom",
                        "config": {"plugins": [], "plugin_specs": {}},
                    },
                    "memory": {
                        "preset": "custom",
                        "config": {"plugins": [], "plugin_specs": {}},
                    },
                    "frames": [{"image_path": "frame_0.png", "timestamp_ms": 5000}],
                }
            )
        )
        memory = self.inspect("memory", self.frames, "--record")
        self.assertEqual(memory["selections"]["perception"]["plugins"], [])
        self.assertEqual(memory["selections"]["memory"]["plugins"], [])
        self.assertEqual(memory["frames"][-1]["record"]["plugins"], [])
        replay = self.inspect("memory", Path(memory["run_dir"]))
        self.assertEqual(replay["selections"]["perception"]["plugins"], [])
        self.assertEqual(replay["selections"]["memory"]["plugins"], [])
        perception = self.inspect("perception", Path(memory["run_dir"]))
        self.assertEqual(perception["selections"]["perception"]["plugins"], [])
        self.assertEqual(perception["frames"][0]["record"]["plugin_runs"], [])
        override = self.inspect(
            "perception", Path(memory["run_dir"]), "--plugin", "frame"
        )
        self.assertEqual(override["selections"]["perception"]["plugins"], ["frame"])

    def test_other_report_metadata_does_not_replace_image_directory_source(self) -> None:
        (self.frames / "report.json").write_text(json.dumps({"results": []}))
        inspected = self.inspect("memory", self.frames)
        self.assertEqual(inspected["source"]["frame_count"], 3)
        self.assertEqual(
            [frame["timestamp_ms"] for frame in inspected["frames"]], [0, 1000, 2000]
        )

    def test_legacy_summary_only_memory_recording_uses_defaults_and_copied_images(
        self,
    ) -> None:
        (self.frames / "report.json").write_text(
            json.dumps(
                {
                    "schema": "memory_inspect_v0",
                    "perception": {
                        "preset": "multi_obstruction",
                        "plugins": ["multi_obstruction_tracks"],
                    },
                    "memory": {
                        "preset": "recency_ledger",
                        "plugins": ["bounded_evidence"],
                    },
                    "frames": [
                        {"frame_id": "previous", "timestamp_ms": 5000, "plugins": []}
                    ],
                }
            )
        )
        inspected = self.inspect("memory", self.frames)
        self.assertEqual(inspected["source"]["frame_count"], 3)
        self.assertEqual(inspected["selections"]["perception"]["preset"], "lightweight_observer")
        self.assertEqual(inspected["selections"]["perception"]["plugins"], ["frame", "floor_plane"])


if __name__ == "__main__":
    unittest.main()
