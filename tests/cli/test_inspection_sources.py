from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from cli.automa_cli.workbench_runner import ImageReplayRunner
from cli.automa_cli.workbench_source import normalize_image_directory
from tests.support.cli_runner import run_automa


def frame_sequence(frames: list[dict]) -> list[tuple]:
    return [
        (
            frame["frame_id"],
            frame["frame_index"],
            frame.get("timestamp_ms", frame.get("captured_at_ms")),
            frame.get("absence_reason"),
        )
        for frame in frames
    ]


class InspectionSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "images"
        self.source.mkdir()
        for name in ("B.png", "a.png"):
            Image.new("RGB", (48, 32), (25, 35, 45)).save(self.source / name)
        self.env = {
            "AUTOMA_PERCEPTION_INSPECT_ROOT": str(self.root / "perception"),
            "AUTOMA_MEMORY_INSPECT_ROOT": str(self.root / "memory"),
        }

    def inspect(self, step: str, source: Path, *flags: str, exit_code: int = 0) -> dict:
        result = run_automa(
            "vehicles",
            step,
            "inspect",
            str(source),
            *flags,
            "--json",
            extra_env=self.env,
            check=False,
        )
        self.assertEqual(result.returncode, exit_code, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def assert_shared_sequence(self, source: Path, *flags: str) -> tuple[dict, dict]:
        expected = normalize_image_directory(source)
        perception = self.inspect(
            "perception",
            source,
            *flags,
            exit_code=1 if any(frame.absent for frame in expected.frames) else 0,
        )
        memory = self.inspect("memory", source, *flags)
        sequence = frame_sequence([frame.to_dict() for frame in expected.frames])
        self.assertEqual(frame_sequence(perception["frames"]), sequence)
        self.assertEqual(frame_sequence(memory["frames"]), sequence)
        return perception, memory

    def test_image_order_identity_and_timing_match_workbench_and_survive_recording(
        self,
    ) -> None:
        for image, seconds in ((self.source / "a.png", 10), (self.source / "B.png", 5)):
            os.utime(image, (seconds, seconds))
        perception, memory = self.assert_shared_sequence(self.source, "--record")
        self.assertEqual(
            [frame["captured_at_ms"] for frame in perception["frames"]], [0, 1000]
        )
        self.assertEqual(
            [Path(frame["image_path"]).name for frame in perception["frames"]],
            ["a.png", "B.png"],
        )
        for recorded in (perception, memory):
            self.assert_shared_sequence(Path(recorded["run_dir"]))

    def test_camera_manifest_and_recordings_preserve_dropout_order_and_timing(
        self,
    ) -> None:
        (self.source / "manifest.json").write_text(
            json.dumps(
                {
                    "source_id": "camera.fixture",
                    "camera_frames": [
                        {
                            "frame_id": "camera-b",
                            "frame_index": 7,
                            "captured_at_ms": 5000,
                            "image": "B.png",
                        },
                        {
                            "frame_id": "dropout",
                            "frame_index": 9,
                            "captured_at_ms": 5000,
                            "absence_reason": "camera missing",
                        },
                        {
                            "frame_id": "camera-a",
                            "frame_index": 12,
                            "captured_at_ms": 5800,
                            "image": "a.png",
                        },
                    ],
                }
            )
        )
        perception, memory = self.assert_shared_sequence(self.source, "--record")
        dropout = perception["frames"][1]
        self.assertEqual(dropout["status"], "unavailable")
        self.assertEqual(dropout["plugin_runs"], [])
        self.assertEqual(perception["summary"]["failed_frames"], 1)
        self.assertEqual(memory["frames"][1]["observation"]["things"], 0)
        self.assertEqual(memory["frames"][1]["observation"]["signals"], 0)
        temporal = self.inspect(
            "perception",
            self.source,
            "--preset",
            "visual_observer",
            exit_code=1,
        )
        for index in (0, 2):
            runs = {
                run["plugin_id"]: run
                for run in temporal["frames"][index]["plugin_runs"]
            }
            self.assertEqual(runs["motion_tracks"]["status"], "warming_up")
        for recorded in (perception, memory):
            recorded_source = Path(recorded["run_dir"])
            self.assert_shared_sequence(recorded_source)
            runner = ImageReplayRunner(recorded_source, cadence_ms=0, loop=False)
            self.addCleanup(runner.close)
            runner.start()
            state = runner.wait(5)
            self.assertEqual(state["phase"], "completed", state.get("failure"))
            self.assertEqual(state["progress"]["completed"], 3)
            self.assertEqual(
                [item["frame"]["frame_id"] for item in state["timeline"]],
                ["camera-b", "dropout", "camera-a"],
            )
            self.assertTrue(state["timeline"][1]["frame"]["absent"])

    def test_recording_with_only_absent_frames_can_be_inspected_and_replayed(
        self,
    ) -> None:
        (self.source / "report.json").write_text(
            json.dumps(
                {
                    "schema": "memory_inspect_v0",
                    "frames": [
                        {
                            "frame_id": "dropout",
                            "frame_index": 3,
                            "timestamp_ms": 5000,
                            "image_path": None,
                            "absence_reason": "camera missing",
                        },
                    ],
                }
            )
        )
        perception, memory = self.assert_shared_sequence(self.source, "--record")
        for recorded in (perception, memory):
            self.assert_shared_sequence(Path(recorded["run_dir"]))

    def test_both_commands_expose_the_workbench_frame_limit_override(self) -> None:
        count = 513
        (self.source / "manifest.json").write_text(
            json.dumps(
                {
                    "frames": [{"image_path": "a.png"} for _ in range(count)],
                }
            )
        )
        for step in ("perception", "memory"):
            with self.subTest(step=step):
                result = run_automa(
                    "vehicles",
                    step,
                    "inspect",
                    str(self.source),
                    "--record",
                    "--json",
                    extra_env=self.env,
                    check=False,
                )
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertIn("max_frames is 512", result.stdout)
                self.assertFalse((self.root / step).exists())
                report = self.inspect(step, self.source, "--max-frames", str(count))
                self.assertEqual(len(report["frames"]), count)
                help_text = run_automa("vehicles", step, "inspect", "--help").stdout
                self.assertIn("--max-frames", help_text)

    def test_operation_captures_keep_before_after_order_for_all_consumers(self) -> None:
        frames = self.source / "frames"
        frames.mkdir()
        for name in ("00_before.png", "00_after.png"):
            Image.new("RGB", (48, 32), (25, 35, 45)).save(frames / name)
        (self.source / "report.json").write_text(
            json.dumps(
                {
                    "results": [
                        {
                            "before_capture": {
                                "path": "/previous/run/frames/00_before.png"
                            },
                            "after_capture": {
                                "path": "/previous/run/frames/00_after.png"
                            },
                        }
                    ],
                }
            )
        )
        perception, _ = self.assert_shared_sequence(self.source)
        self.assertEqual(
            [Path(frame["image_path"]).name for frame in perception["frames"]],
            ["00_before.png", "00_after.png"],
        )

    def test_invalid_sources_fail_before_recording_or_plugin_construction(self) -> None:
        (self.source / "a.png").write_bytes(b"not an image")
        for step in ("perception", "memory"):
            with self.subTest(step=step):
                result = run_automa(
                    "vehicles",
                    step,
                    "inspect",
                    str(self.source),
                    "--record",
                    "--json",
                    extra_env=self.env,
                    check=False,
                )
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertIn(f"Could not read {step} inspect source:", result.stdout)
                self.assertIn("not decodable", result.stdout)
                self.assertFalse((self.root / step).exists())
