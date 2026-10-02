from __future__ import annotations
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from cli.automa_cli.workbench import normalize_image_directory
from tests.cli.workbench_fixtures import (
    BlockingSecondMapper,
    DecisionFixtureMapper,
    ErrorMemory,
    ErrorStatusMapper,
    FixtureMapper,
    ImageReplayRunner,
    _make_images,
    image_source,
    write_manifest,
)


class WorkbenchTests(unittest.TestCase):
    def test_directory_adapter_loads_ordered_camera_frame_stream_manifest(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            frame_root = root / "frames"
            frame_root.mkdir()
            _make_images(frame_root, 2)
            write_manifest(root, {
                "camera_frames": [
                    {
                        "frame_id": "camera-10",
                        "frame_index": 10,
                        "captured_at_ms": 1000,
                        "image": "frames/frame_00.png",
                    },
                    {
                        "frame_id": "camera-13",
                        "frame_index": 13,
                        "captured_at_ms": 1080,
                        "image": "frames/frame_01.png",
                    },
                ]
            })

            feed = normalize_image_directory(root)

        self.assertEqual(
            [frame.frame_id for frame in feed.frames], ["camera-10", "camera-13"]
        )
        self.assertEqual([frame.frame_index for frame in feed.frames], [10, 13])
        self.assertEqual([frame.timestamp_ms for frame in feed.frames], [1000, 1080])
        self.assertEqual(
            [frame.image_path.name for frame in feed.frames if frame.image_path],
            ["frame_00.png", "frame_01.png"],
        )

    def test_default_frame_limit_accepts_a_high_rate_camera_capture(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            frame_root = root / "frames"
            frame_root.mkdir()
            _make_images(frame_root, 1)
            camera_frames = [
                {
                    "frame_id": f"camera-{index}",
                    "frame_index": index,
                    "captured_at_ms": 1000 + index * 50,
                    "image": "frames/frame_00.png",
                }
                for index in range(269)
            ]
            write_manifest(root, {"camera_frames": camera_frames})

            feed = normalize_image_directory(root)

        self.assertEqual(len(feed.frames), 269)
        self.assertEqual(feed.frames[-1].frame_id, "camera-268")

    def test_directory_adapter_honors_manifest_order_and_absence(self) -> None:
        with TemporaryDirectory() as directory:
            workspace = Path(directory)
            root = workspace / "lab/runs/fixture-run"
            image_root = workspace / "lab/runs/capture"
            root.mkdir(parents=True)
            image_root.mkdir(parents=True)
            _make_images(image_root, 2)
            write_manifest(root, {
                "source_id": "fixture.sequence",
                "run_dir": "lab/runs/fixture-run",
                "source": {
                    "kind": "images",
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
            }, name="run.json")

            feed = normalize_image_directory(root)

        self.assertEqual(feed.source_id, "fixture.sequence")
        self.assertEqual(
            [frame.frame_id for frame in feed.frames], ["capture-b", "capture-dropout"]
        )
        self.assertEqual(feed.frames[0].image_path.name, "frame_01.png")
        self.assertTrue(feed.frames[1].absent)
        self.assertEqual(feed.frames[1].absence_reason, "camera dropout")


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
            self.assertIsNone(broken_state["steps"]["perception"])

    def test_runner_fails_closed_on_mapper_and_memory_errors(self) -> None:
        with image_source(2) as root:
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
            self.assertEqual(state["steps"]["perception"]["status"], "error")

            memory_mapper = FixtureMapper()
            memory_runner = ImageReplayRunner(
                root,
                cadence_ms=0,
                mapper_factory=lambda: memory_mapper,
                memory_step_factory=lambda: ErrorMemory(),
            )
            memory_started = memory_runner.start()
            memory_state = (
                memory_runner.wait(5)
                if memory_started["phase"] == "running"
                else memory_started
            )
            self.assertEqual(memory_state["phase"], "failed")
            self.assertEqual(memory_state["failure_boundary"], "memory")
            self.assertIsNone(memory_state["steps"]["memory"])
            self.assertIsNone(memory_state["steps"]["decision"])
            self.assertIn("injected memory failure", memory_state["failure"]["message"])
            self.assertEqual(len(memory_mapper.calls), 1)
            self.assertEqual(memory_state["progress"]["completed"], 0)

    def test_runner_uses_existing_pipeline_and_reports_memory_effects(self) -> None:
        with image_source(2) as root:
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
            state["steps"]["observation"]["metadata"]["source"], "workbench.image_replay.v1"
        )
        self.assertEqual(state["steps"]["memory"]["health"], "healthy")
        self.assertGreaterEqual(state["steps"]["memory"]["record_count"], 2)
        self.assertNotIn("frames", state["source"])
        self.assertNotIn("perception", state["timeline"][0])
        self.assertEqual(first_detail["steps"]["perception"]["status"], "ok")
        self.assertIsNotNone(first_detail["steps"]["observation"]["observation_id"])
        self.assertEqual(first_detail["steps"]["memory"]["health"], "healthy")
        self.assertTrue(state["timeline"][0]["memory_effect"]["added"])
        self.assertTrue(state["cleanup"]["source_read_only"])
        self.assertFalse(state["cleanup"]["movement_control"])
        self.assertFalse(state["machine_detail"]["side_effects"]["simulator"])

    def test_default_selection_is_the_lightweight_observer_preset(self) -> None:
        with image_source(1) as root:
            runner = ImageReplayRunner(root, cadence_ms=0)
            runner.start()
            state = runner.wait(5)

        self.assertEqual(state["phase"], "completed")
        self.assertEqual(
            state["machine_detail"]["pipeline"]["perception_preset"],
            "lightweight_observer",
        )

    def test_runner_persists_frame_correlated_decision_playback(self) -> None:
        with image_source(2) as root:
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

        decision = state["steps"]["decision"]
        first_decision = detail["steps"]["decision"]
        self.assertEqual(state["phase"], "completed")
        self.assertEqual(decision["frame_id"], state["current_frame"]["frame_id"])
        self.assertEqual(decision["plan"]["status"], "selected")
        self.assertTrue(
            decision["plan"]["selected_proposal_id"].startswith(
                "avoid_recent_obstruction:"
            )
        )
        self.assertEqual(decision["authority"]["proposed"]["steering"], 1.0)
        self.assertFalse(decision["authority"]["proposed_applied"])
        self.assertEqual(
            state["timeline"][0]["decision"]["selected_proposal_id"],
            first_decision["plan"]["selected_proposal_id"],
        )
        self.assertFalse(state["timeline"][0]["decision"]["proposed_applied"])
        self.assertEqual(first_decision["frame_id"], frame_id)
        self.assertFalse(first_decision["authority"]["proposed_applied"])
        self.assertEqual(
            state["machine_detail"]["pipeline"]["decision_steps"],
            {"proposal": ["avoid_recent_obstruction"], "plan": ["highest_confidence"], "action": ["hold"]},
        )
        decision_config = state["machine_detail"]["pipeline"]["decision_config"]
        self.assertFalse(decision_config["proposed_applied"])
        self.assertEqual(decision_config["plugins"], ["avoid_recent_obstruction"])
        proposal_config = decision_config["plugin_configs"]["avoid_recent_obstruction"]
        self.assertEqual(proposal_config["steer_magnitude"], 1.0)
        self.assertEqual(
            proposal_config["accepted_kinds"],
            ["floor_boundary", "obstacle", "obstruction_evidence"],
        )

    def test_absence_does_not_invoke_perception_or_fabricate_image(self) -> None:
        with image_source(1) as root:
            write_manifest(root, {
                "frames": [
                    {"frame_id": "present", "image_path": "frame_00.png"},
                    {
                        "frame_id": "absent",
                        "absent": True,
                        "absence_reason": "dropout",
                    },
                ]
            })
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
        self.assertEqual(state["steps"]["observation"]["metadata"]["absence_reason"], "dropout")

    def test_public_state_keeps_frame_and_pipeline_payload_paired(self) -> None:
        with image_source(2) as root:
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
