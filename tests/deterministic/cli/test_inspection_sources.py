from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from cli.automa_cli.workbench.runner import ImageReplayRunner
from cli.automa_cli.replay.source import normalize_image_directory
from cli.automa_cli.replay.models import SourceValidationError
from tests.shared.cli.invoke import invoke_automa
from tests.shared.cli.workbench_fixtures import replay_states


def frame_sequence(frames: list[dict]) -> list[tuple]:
    return [
        (
            frame["frame_id"],
            frame["frame_index"],
            frame["timestamp_ms"],
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
        result = invoke_automa(
            "plugins",
            "inspect", step,
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
            exit_code=0,
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
            [frame["timestamp_ms"] for frame in perception["frames"]], [0, 1000]
        )
        self.assertEqual(
            [Path(frame["image_path"]).name for frame in perception["frames"]],
            ["frame_000000.png", "frame_000001.png"],
        )
        for recorded in (perception, memory):
            self.assert_shared_sequence(Path(recorded["run_dir"]))

    def test_manifest_and_recordings_preserve_dropout_order_and_timing(
        self,
    ) -> None:
        (self.source / "manifest.json").write_text(
            json.dumps(
                {
                    "source_id": "camera.fixture",
                    "frames": [
                        {
                            "frame_id": "camera-b",
                            "frame_index": 7,
                            "timestamp_ms": 5000,
                            "image_path": "B.png",
                        },
                        {
                            "frame_id": "dropout",
                            "frame_index": 9,
                            "timestamp_ms": 5000,
                            "absence_reason": "camera missing",
                        },
                        {
                            "frame_id": "camera-a",
                            "frame_index": 12,
                            "timestamp_ms": 5800,
                            "image_path": "a.png",
                        },
                    ],
                }
            )
        )
        perception, memory = self.assert_shared_sequence(self.source, "--record")
        dropout = perception["frames"][1]
        self.assertIsNone(dropout["record"])
        self.assertIsNone(memory["frames"][1]["observation"])
        temporal = self.inspect(
            "perception",
            self.source,
            "--preset",
            "visual_observer",
            exit_code=0,
        )
        for index in (0, 2):
            runs = {
                run["plugin_id"]: run
                for run in temporal["frames"][index]["record"]["plugin_runs"]
            }
            self.assertEqual(runs["motion_tracks"]["status"], "warming_up")
        for recorded in (perception, memory):
            recorded_source = Path(recorded["run_dir"])
            self.assert_shared_sequence(recorded_source)
            runner = ImageReplayRunner(recorded_source, cadence_ms=0, loop=False)
            self.addCleanup(runner.close)
            states = replay_states(runner)
            state = states[-1]
            self.assertEqual(state["phase"], "completed", state.get("failure"))
            self.assertEqual(state["progress"]["completed"], 3)
            self.assertEqual(
                [item["current_frame"]["frame_id"] for item in states],
                ["camera-b", "dropout", "camera-a"],
            )
            self.assertTrue(states[1]["current_frame"]["absent"])

    def test_recording_with_only_absent_frames_can_be_inspected_and_replayed(
        self,
    ) -> None:
        (self.source / "manifest.json").write_text(
            json.dumps(
                {
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
                result = invoke_automa(
                    "plugins",
                    "inspect", step,
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
                help_text = invoke_automa("plugins", "inspect", step, "--help").stdout
                self.assertIn("--max-frames", help_text)

    def test_invalid_manifests_never_fall_back_to_image_discovery(self) -> None:
        cases = [
            ('{"frames":', "could not read manifest"),
            ('[]', "must contain a JSON object"),
            ('{}', "frames must be a list"),
            ('{"frames": {}}', "frames must be a list"),
            ('{"frames": []}', "contains no frames"),
            ('{"camera_frames": [{"image": "a.png"}]}', "frames must be a list"),
            ('{"results": [{"before_capture": {"path": "a.png"}}]}', "frames must be a list"),
            ('{"frames": ["a.png"]}', "frame 0 must be an object"),
            ('{"frames": [{"image": "a.png"}]}', "has no image or absence reason"),
            ('{"frames": [{"absent": true}]}', "has no image or absence reason"),
        ]
        for contents, message in cases:
            with self.subTest(contents=contents):
                (self.source / "manifest.json").write_text(contents)
                with self.assertRaisesRegex(SourceValidationError, message):
                    normalize_image_directory(self.source)

    def test_manifest_paths_must_be_relative_and_contained_without_symlinks(self) -> None:
        (self.source / "linked.png").symlink_to(self.source / "a.png")
        cases = [
            (str(self.source / "a.png"), "must be relative"),
            ("/original/machine/run/frames/a.png", "must be relative"),
            ("../images/a.png", "must remain inside"),
            ("linked.png", "may not traverse symlinks"),
        ]
        for image_path, message in cases:
            with self.subTest(image_path=image_path):
                (self.source / "manifest.json").write_text(json.dumps({
                    "run_dir": "/original/machine/run",
                    "frames": [{"image_path": image_path}],
                }))
                with self.assertRaisesRegex(SourceValidationError, message):
                    normalize_image_directory(self.source)

    def test_invalid_manifest_fails_inspect_before_writing_a_recording(self) -> None:
        (self.source / "manifest.json").write_text('{"frames": ["a.png"]}')
        for step in ("perception", "memory"):
            with self.subTest(step=step):
                result = invoke_automa(
                    "plugins", "inspect", step, str(self.source), "--record",
                    extra_env=self.env, check=False,
                )
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertIn("manifest frame 0 must be an object", result.stdout)
                self.assertFalse((self.root / step).exists())

    def test_invalid_sources_fail_before_recording_or_plugin_construction(self) -> None:
        (self.source / "a.png").write_bytes(b"not an image")
        for step in ("perception", "memory"):
            with self.subTest(step=step):
                result = invoke_automa(
                    "plugins",
                    "inspect", step,
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


if __name__ == "__main__":
    unittest.main()
