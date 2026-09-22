from __future__ import annotations
import json
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from cli.automa_cli.workbench import ReplayActionError, WorkbenchServer
from tests.cli.workbench_fixtures import (
    FixtureMapper,
    ImageReplayRunner,
    _make_images,
    _wait_until,
)


class RecordingMapper(FixtureMapper):
    def __init__(self) -> None:
        super().__init__()
        self.priors: list[object] = []

    def perceive(self, request):
        metadata = getattr(request, "metadata", {}) or {}
        self.priors.append(metadata.get("prior_memory"))
        return super().perceive(request)


class WorkbenchTests(unittest.TestCase):
    def test_pause_resume_step_reset_and_stale_run_are_server_owned(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _make_images(root, 3)
            runner = ImageReplayRunner(root, cadence_ms=1000)
            first = runner.start()
            run_id = first["run_id"]
            _wait_until(lambda: len(runner.state()["timeline"]) >= 1)
            paused = runner.dispatch("pause", run_id=run_id)
            self.assertEqual(paused["phase"], "paused")
            with self.assertRaises(ReplayActionError):
                runner.dispatch("pause", run_id="stale-run")
            stepped = runner.dispatch("step", run_id=run_id)
            self.assertEqual(stepped["phase"], "paused")
            self.assertEqual(len(stepped["timeline"]), 2)
            runner.dispatch("resume", run_id=run_id)
            completed = runner.wait(5)
            self.assertEqual(completed["phase"], "completed")
            with self.assertRaises(ReplayActionError):
                runner.dispatch("reset")
            reset = runner.dispatch("reset", run_id=run_id)
            self.assertEqual(reset["phase"], "idle")
            self.assertIsNone(reset["run_id"])
            self.assertEqual(reset["progress"]["completed"], 0)
            self.assertEqual(reset["progress"]["total"], 3)
            self.assertIsNotNone(reset["source"])
            self.assertEqual(reset["timeline"], [])
            restarted = runner.start(cadence_ms=0)
            completed_again = runner.wait(5)

        self.assertEqual(restarted["phase"], "running")
        self.assertEqual(completed_again["phase"], "completed")
        self.assertEqual(completed_again["progress"]["completed"], 3)

    def test_ordered_replay_does_not_skip_ahead_and_applies_plugin_parameters(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _make_images(root, 4)
            mapper = FixtureMapper()
            editor = {
                "plugin_id": "fixture",
                "title": "Fixture",
                "fields": [
                    {
                        "key": "canny_low",
                        "label": "Canny low",
                        "kind": "int",
                        "min": 1,
                        "max": 80,
                        "value": 30,
                    }
                ],
            }
            runner = ImageReplayRunner(
                root,
                cadence_ms=5000,
                mapper_factory=lambda: mapper,
                ordered_replay=True,
                parameter_editor=editor,
                plugin_config={"fixture": {"canny_low": 30}},
            )
            started = runner.start()
            run_id = started["run_id"]
            _wait_until(lambda: len(runner.state()["timeline"]) >= 1)
            paused = runner.dispatch("pause", run_id=run_id)
            self.assertIn("apply_plugin_parameters", paused["controls"]["allowed_actions"])
            calls_after_first = len(mapper.calls)
            sought = runner.dispatch("seek", run_id=run_id, position=2)
            self.assertEqual(sought["current_frame"]["position"], 2)
            self.assertGreaterEqual(len(mapper.calls), calls_after_first + 2)
            applied = runner.dispatch(
                "apply_plugin_parameters",
                run_id=run_id,
                plugin_parameters={"canny_low": 12},
            )
            self.assertEqual(applied["current_frame"]["position"], 2)
            self.assertEqual(applied["parameter_editor"]["fields"][0]["value"], 12)
            self.assertGreater(mapper.reset_count, 0)

    def test_sequential_replay_passes_previous_memory_to_perception(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _make_images(root, 2)
            mapper = RecordingMapper()
            runner = ImageReplayRunner(
                root,
                cadence_ms=0,
                mapper_factory=lambda: mapper,
            )

            runner.start()
            completed = runner.wait(5)

        self.assertEqual(completed["phase"], "completed")
        self.assertEqual(len(mapper.priors), 2)
        self.assertIsNone(mapper.priors[0])
        self.assertIsInstance(mapper.priors[1], dict)
        self.assertIn("records", mapper.priors[1])

    def test_seek_jumps_current_frame_and_reuses_processed_history(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _make_images(root, 4)
            mapper = FixtureMapper()
            runner = ImageReplayRunner(
                root,
                cadence_ms=5000,
                mapper_factory=lambda: mapper,
            )
            started = runner.start()
            run_id = started["run_id"]
            _wait_until(lambda: len(runner.state()["timeline"]) >= 1)
            paused = runner.dispatch("pause", run_id=run_id)
            first_id = paused["current_frame"]["frame_id"]
            calls_after_first = len(mapper.calls)
            self.assertIn("seek", paused["controls"]["allowed_actions"])

            sought = runner.dispatch("seek", run_id=run_id, position=2)
            self.assertEqual(sought["phase"], "paused")
            self.assertEqual(sought["current_frame"]["position"], 2)
            self.assertEqual(sought["position"], 3)
            self.assertEqual(len(mapper.calls), calls_after_first + 1)
            self.assertEqual(
                sought["decision"]["frame_id"],
                sought["current_frame"]["frame_id"],
            )
            self.assertEqual(
                sought["current_frame"]["frame_id"],
                sought["timeline"][-1]["frame"]["frame_id"],
            )

            cached = runner.dispatch("seek", run_id=run_id, position=0)
            self.assertEqual(cached["phase"], "paused")
            self.assertEqual(cached["current_frame"]["frame_id"], first_id)
            self.assertEqual(cached["current_frame"]["position"], 0)
            self.assertEqual(cached["decision"]["frame_id"], first_id)
            self.assertEqual(len(mapper.calls), calls_after_first + 1)

            with self.assertRaises(ReplayActionError):
                runner.dispatch("seek", run_id=run_id, position=99)
            with self.assertRaises(ReplayActionError):
                runner.dispatch("seek", run_id=run_id)
            runner.dispatch("cancel", run_id=run_id)

        idle = ImageReplayRunner()
        with self.assertRaises(ReplayActionError):
            idle.dispatch("seek", run_id="missing", position=0)

    def test_loop_playback_rewinds_instead_of_completing(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _make_images(root, 2)
            mapper = FixtureMapper()
            runner = ImageReplayRunner(
                root,
                cadence_ms=0,
                mapper_factory=lambda: mapper,
                loop=True,
            )
            started = runner.start()
            run_id = started["run_id"]
            self.assertTrue(started["controls"]["loop"])
            _wait_until(lambda: len(mapper.calls) >= 3)
            live = runner.state()
            self.assertEqual(live["phase"], "running")
            self.assertLessEqual(len(live["timeline"]), 2)
            runner.dispatch("set_loop", run_id=run_id, loop=False)
            finished = runner.wait(5)
            self.assertEqual(finished["phase"], "completed")
            self.assertFalse(finished["controls"]["loop"])

    def test_seek_while_running_pauses_and_serves_frame_bytes_by_position(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _make_images(root, 4)
            runner = ImageReplayRunner(root, cadence_ms=5000)
            server = WorkbenchServer(runner).start()
            self.addCleanup(server.stop)
            base = server.url
            self.assertIsNotNone(base)

            def post(payload: dict[str, object]) -> dict[str, object]:
                body = json.dumps(payload).encode("utf-8")
                return json.loads(
                    urlopen(
                        Request(
                            base + "api/action",
                            data=body,
                            headers={"Content-Type": "application/json"},
                            method="POST",
                        ),
                        timeout=10,
                    ).read()
                )

            started = post(
                {"action": "start", "source_dir": str(root), "cadence_ms": 5000}
            )
            run_id = started["state"]["run_id"]
            _wait_until(lambda: len(runner.state()["timeline"]) >= 1)
            sought = post({"action": "seek", "run_id": run_id, "position": 2})
            self.assertEqual(sought["state"]["phase"], "paused")
            self.assertEqual(sought["state"]["current_frame"]["position"], 2)
            query = urlencode({"run_id": run_id, "position": "2"})
            frame = urlopen(base + "api/frame?" + query, timeout=2)
            self.assertEqual(frame.status, 200)
            self.assertTrue(frame.read())
            post({"action": "cancel", "run_id": run_id})

    def test_realtime_pace_honors_recorded_frame_timestamps(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _make_images(root, 3)
            (root / "manifest.json").write_text(
                json.dumps(
                    {
                        "source_id": "timed.fixture",
                        "frames": [
                            {
                                "frame_id": "first",
                                "timestamp_ms": 0,
                                "image_path": "frame_00.png",
                            },
                            {
                                "frame_id": "second",
                                "timestamp_ms": 80,
                                "image_path": "frame_01.png",
                            },
                            {
                                "frame_id": "third",
                                "timestamp_ms": 160,
                                "image_path": "frame_02.png",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            runner = ImageReplayRunner(
                root,
                cadence_ms=0,
                pace="realtime",
                mapper_factory=FixtureMapper,
            )
            started = runner.start()
            self.assertEqual(started["controls"]["pace"], "realtime")
            _wait_until(lambda: len(runner.state()["timeline"]) >= 1)
            first_seen = time.monotonic()
            _wait_until(lambda: len(runner.state()["timeline"]) >= 2)
            second_seen = time.monotonic()
            completed = runner.wait(3)

        self.assertGreaterEqual(second_seen - first_seen, 0.06)
        self.assertEqual(completed["phase"], "completed")
        self.assertEqual(completed["controls"]["pace"], "realtime")
