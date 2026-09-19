from __future__ import annotations
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile
from cli.automa_cli.workbench import SourceValidationError, normalize_image_directory
from tests.cli.workbench_fixtures import (
    BlockingSecondMapper,
    DecisionFixtureMapper,
    ErrorMemory,
    ErrorStatusMapper,
    FixtureMapper,
    ImageReplayRunner,
    _make_images,
)

DECISION_PLAYBACK_SOURCE_ARCHIVE = Path(
    "tests/cli/sources/images/chase-decision-playback-steering-left-right/capture.zip"
)
WORKBENCH_PLUGIN_DIR = Path("lab/plugins/perception")


class WorkbenchTests(unittest.TestCase):
    def test_directory_adapter_honors_manifest_order_and_absence(self) -> None:
        with TemporaryDirectory() as directory:
            workspace = Path(directory)
            root = workspace / "lab/plugins/perception/example/runs/fixture-run"
            image_root = workspace / "lab/runs/capture"
            root.mkdir(parents=True)
            image_root.mkdir(parents=True)
            _make_images(image_root, 2)
            (root / "run.json").write_text(
                json.dumps(
                    {
                        "source_id": "fixture.sequence",
                        "run_dir": "lab/plugins/perception/example/runs/fixture-run",
                        "source": {
                            "kind": "apply",
                            "path": "/previous/location/auto-driving/lab/runs/capture",
                        },
                        "frames": [
                            {
                                "frame_id": "capture-b",
                                "frame_index": 5,
                                "captured_at_ms": 500,
                                "image_path": (
                                    "/previous/location/auto-driving/lab/runs/capture/"
                                    "frame_01.png"
                                ),
                            },
                            {
                                "frame_id": "capture-dropout",
                                "frame_index": 6,
                                "captured_at_ms": 600,
                                "absent": True,
                                "absence_reason": "camera dropout",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            feed = normalize_image_directory(root)

        self.assertEqual(feed.source_id, "fixture.sequence")
        self.assertEqual(
            [frame.frame_id for frame in feed.frames], ["capture-b", "capture-dropout"]
        )
        self.assertEqual(feed.frames[0].image_path.name, "frame_01.png")
        self.assertTrue(feed.frames[1].absent)
        self.assertEqual(feed.frames[1].absence_reason, "camera dropout")

    def test_directory_adapter_rejects_traversal_duplicate_and_unsupported_inputs(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _make_images(root, 1)
            (root / "manifest.json").write_text(
                json.dumps(
                    {
                        "frames": [
                            {"frame_id": "same", "image_path": "frame_00.png"},
                            {"frame_id": "same", "image_path": "frame_00.png"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(SourceValidationError):
                normalize_image_directory(root)

            (root / "manifest.json").write_text(
                json.dumps({"frames": [{"image_path": "../outside.png"}]}),
                encoding="utf-8",
            )
            with self.assertRaises(SourceValidationError):
                normalize_image_directory(root)

            (root / "manifest.json").unlink()
            (root / "bad.gif").write_bytes(b"not an image")
            with self.assertRaises(SourceValidationError):
                normalize_image_directory(root)

    def test_directory_adapter_rejects_empty_over_limit_and_nonincreasing_sources(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(SourceValidationError):
                normalize_image_directory(root)

            _make_images(root, 3)
            with self.assertRaises(SourceValidationError):
                normalize_image_directory(root, max_frames=2)
            with self.assertRaises(SourceValidationError):
                normalize_image_directory(root, max_image_bytes=10)

            (root / "manifest.json").write_text(
                json.dumps(
                    {
                        "frames": [
                            {
                                "frame_id": "later",
                                "frame_index": 2,
                                "timestamp_ms": 20,
                                "image_path": "frame_00.png",
                            },
                            {
                                "frame_id": "earlier",
                                "frame_index": 1,
                                "timestamp_ms": 30,
                                "image_path": "frame_01.png",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(SourceValidationError):
                normalize_image_directory(root)

            (root / "manifest.json").write_text(
                json.dumps(
                    {
                        "frames": [
                            {
                                "frame_id": "first",
                                "frame_index": 1,
                                "timestamp_ms": 40,
                                "image_path": "frame_00.png",
                            },
                            {
                                "frame_id": "second",
                                "frame_index": 2,
                                "timestamp_ms": 40,
                                "image_path": "frame_01.png",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(SourceValidationError):
                normalize_image_directory(root)

    def test_runner_refuses_invalid_and_undecodable_sources_before_pipeline(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            broken = Path(directory) / "broken"
            broken.mkdir()
            (broken / "broken.png").write_bytes(b"not an image")
            broken_mapper = FixtureMapper()
            broken_state = ImageReplayRunner(
                broken,
                cadence_ms=0,
                mapper_factory=lambda: broken_mapper,
            ).start()
            self.assertEqual(broken_state["phase"], "failed")
            self.assertEqual(broken_state["failure_boundary"], "source")
            self.assertEqual(broken_mapper.calls, [])
            self.assertIsNone(broken_state["perception"])

    def test_runner_fails_closed_on_mapper_and_memory_errors(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _make_images(root, 1)
            error_mapper = ErrorStatusMapper()
            runner = ImageReplayRunner(
                root,
                cadence_ms=0,
                mapper_factory=lambda: error_mapper,
            )
            started = runner.start()
            state = runner.wait(5) if started["phase"] == "running" else started
            self.assertEqual(state["phase"], "failed")
            self.assertEqual(state["failure_boundary"], "perception")
            self.assertEqual(state["perception"]["status"], "error")

            memory_mapper = FixtureMapper()
            memory_runner = ImageReplayRunner(
                root,
                cadence_ms=0,
                mapper_factory=lambda: memory_mapper,
                memory_stage_factory=lambda: ErrorMemory(),
            )
            memory_started = memory_runner.start()
            memory_state = (
                memory_runner.wait(5)
                if memory_started["phase"] == "running"
                else memory_started
            )
            self.assertEqual(memory_state["phase"], "failed")
            self.assertEqual(memory_state["failure_boundary"], "memory")
            self.assertEqual(memory_state["memory"]["health"], "error")

    def test_runner_uses_existing_pipeline_and_reports_memory_effects(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _make_images(root, 2)
            mapper = FixtureMapper()
            runner = ImageReplayRunner(
                root,
                cadence_ms=0,
                mapper_factory=lambda: mapper,
            )
            runner.start()
            state = runner.wait(5)
            first_frame_id = state["timeline"][0]["frame"]["frame_id"]
            first_detail = runner.frame_detail(first_frame_id, run_id=state["run_id"])

        self.assertEqual(state["phase"], "completed")
        self.assertEqual(state["sequence_id"], "workbench.image_replay.v1")
        self.assertEqual(state["progress"]["completed"], 2)
        self.assertEqual(len(mapper.calls), 2)
        self.assertEqual(
            state["observation"]["metadata"]["source"], "workbench.image_replay.v1"
        )
        self.assertEqual(state["memory"]["health"], "healthy")
        self.assertGreaterEqual(state["memory"]["record_count"], 2)
        self.assertNotIn("frames", state["source"])
        self.assertNotIn("perception", state["timeline"][0])
        self.assertEqual(first_detail["perception"]["status"], "ok")
        self.assertIsNotNone(first_detail["observation"]["observation_id"])
        self.assertEqual(first_detail["memory"]["health"], "healthy")
        self.assertTrue(state["timeline"][0]["memory_effect"]["added"])
        self.assertTrue(state["cleanup"]["source_read_only"])
        self.assertFalse(state["cleanup"]["movement_control"])
        self.assertFalse(state["machine_detail"]["side_effects"]["simulator"])

    def test_runner_persists_frame_correlated_shadow_decision_playback(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _make_images(root, 2)
            mapper = DecisionFixtureMapper()
            runner = ImageReplayRunner(
                root,
                cadence_ms=0,
                mapper_factory=lambda: mapper,
            )
            runner.start()
            state = runner.wait(5)
            frame_id = state["timeline"][0]["frame"]["frame_id"]
            detail = runner.frame_detail(frame_id, run_id=state["run_id"])

        decision = state["decision"]
        first_decision = detail["decision"]
        self.assertEqual(state["phase"], "completed")
        self.assertEqual(decision["frame_id"], state["current_frame"]["frame_id"])
        self.assertEqual(decision["plan"]["status"], "selected")
        self.assertTrue(
            decision["plan"]["selected_proposal_id"].startswith(
                "avoid_recent_obstruction:"
            )
        )
        self.assertEqual(decision["authority"]["proposed"]["steering"], 0.35)
        self.assertFalse(decision["authority"]["proposed_applied"])
        self.assertEqual(
            state["timeline"][0]["decision"]["selected_proposal_id"],
            first_decision["plan"]["selected_proposal_id"],
        )
        self.assertFalse(state["timeline"][0]["decision"]["proposed_applied"])
        self.assertEqual(first_decision["frame_id"], frame_id)
        self.assertFalse(first_decision["authority"]["proposed_applied"])
        self.assertEqual(
            state["machine_detail"]["pipeline"]["decision_engine"],
            "shadow-proposals",
        )
        self.assertFalse(
            state["machine_detail"]["pipeline"]["decision_config"]["proposed_applied"]
        )

    def test_recorded_steering_capture_replays_both_decision_changes(self) -> None:
        expected_phases = [
            "baseline-center",
            "steer-left-forward",
            "left-turn-settle",
            "reverse-to-start-from-left",
            "center-between-turns",
            "steer-right-forward",
            "right-turn-settle",
            "reverse-to-start-from-right",
            "return-to-center",
        ]

        with TemporaryDirectory() as directory:
            capture_dir = Path(directory) / "capture"
            capture_dir.mkdir()
            with ZipFile(DECISION_PLAYBACK_SOURCE_ARCHIVE) as archive:
                archive.extractall(capture_dir)
            manifest = json.loads(
                (capture_dir / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                [item["name"] for item in manifest["capture"]["control_sequence"]],
                expected_phases,
            )

            runner = ImageReplayRunner(
                capture_dir,
                plugin_dir=WORKBENCH_PLUGIN_DIR,
                active_plugin_ids=["floor_continuity_capture"],
                cadence_ms=0,
                max_frames=128,
            )
            started = runner.start()
            try:
                state = runner.wait(30) if started["phase"] == "running" else started
            finally:
                runner.close()

        self.assertEqual(state["phase"], "completed")
        self.assertEqual(
            state["progress"], {"completed": 102, "total": 102, "percent": 100.0}
        )
        self.assertEqual(state["source"]["source_id"], manifest["source_id"])
        self.assertEqual(state["summary"]["perception_status"], "ok")
        self.assertEqual(state["summary"]["memory_health"], "healthy")

        timeline_by_frame_id = {
            item["frame"]["frame_id"]: item for item in state["timeline"]
        }
        phase_by_frame_id = {
            item["frame_id"]: item["annotation"]["control_phase"]
            for item in manifest["frames"]
        }
        left_proposals = [
            item["decision"]["proposed_steering"]
            for frame_id, item in timeline_by_frame_id.items()
            if phase_by_frame_id[frame_id]
            in {"steer-left-forward", "reverse-to-start-from-left"}
            and item["decision"]["proposed_steering"] is not None
        ]
        right_proposals = [
            item["decision"]["proposed_steering"]
            for frame_id, item in timeline_by_frame_id.items()
            if phase_by_frame_id[frame_id]
            in {"steer-right-forward", "reverse-to-start-from-right"}
            and item["decision"]["proposed_steering"] is not None
        ]
        self.assertTrue(any(value < 0 for value in left_proposals))
        self.assertTrue(any(value > 0 for value in right_proposals))
        self.assertTrue(
            all(
                not item["decision"]["proposed_applied"]
                for item in state["timeline"]
            )
        )

    def test_absence_does_not_invoke_perception_or_fabricate_image(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _make_images(root, 1)
            (root / "manifest.json").write_text(
                json.dumps(
                    {
                        "frames": [
                            {"frame_id": "present", "image_path": "frame_00.png"},
                            {
                                "frame_id": "absent",
                                "absent": True,
                                "absence_reason": "dropout",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            mapper = FixtureMapper()
            runner = ImageReplayRunner(
                root,
                cadence_ms=0,
                mapper_factory=lambda: mapper,
            )
            runner.start()
            state = runner.wait(5)

        self.assertEqual(state["phase"], "completed")
        self.assertEqual(len(mapper.calls), 1)
        self.assertTrue(state["timeline"][1]["frame"]["absent"])
        self.assertEqual(state["observation"]["metadata"]["absence_reason"], "dropout")

    def test_public_state_keeps_frame_and_pipeline_payload_paired(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _make_images(root, 2)
            mapper = BlockingSecondMapper()
            runner = ImageReplayRunner(
                root,
                cadence_ms=0,
                mapper_factory=lambda: mapper,
            )
            runner.start()
            self.assertTrue(mapper.second_started.wait(3))
            try:
                active = runner.state()
                self.assertEqual(len(active["timeline"]), 1)
                self.assertEqual(
                    active["current_frame"]["frame_id"],
                    active["timeline"][0]["frame"]["frame_id"],
                )
            finally:
                mapper.release_second.set()
            self.assertEqual(runner.wait(5)["phase"], "completed")
