from __future__ import annotations
import time
import unittest
from functools import partial
from urllib.parse import urlencode
from urllib.request import urlopen
from autonomy.decision_cycle.perception.evidence.values import (
    PerceptionEvidenceBatch,
    PerceptionSignal,
)
from autonomy.decision_cycle.perception.plugin import PerceptionPluginContract
from autonomy.decision_cycle.perception.runner import PerceptionRunner
from cli.automa_cli.memory_report import last_plugin_state
from implementations.decision_cycle.memory.plugins.bounded_evidence.plugin import LEDGER_KEY
from implementations.decision_cycle.memory.shared.evidence_ledger.ledger import EvidenceLedger
from cli.automa_cli.workbench_frames import default_memory_step
from cli.automa_cli.workbench import ReplayActionError
from tests.cli.workbench_fixtures import (
    FixtureMapper,
    ImageReplayRunner,
    _wait_until,
    image_source,
    post_action,
    serve_workbench,
    write_manifest,
)


class RecordingMapper(FixtureMapper):
    def __init__(self) -> None:
        super().__init__()
        self.priors: list[object] = []
        self.legacy_handoff: list[bool] = []

    def perceive(self, request):
        self.legacy_handoff.append("prior_memory" in request.metadata)
        self.priors.append(request.shared_memory.get(LEDGER_KEY))
        return super().perceive(request)


class SharedMemoryProbe:
    plugin_id = "probe"
    contract = PerceptionPluginContract()

    def __init__(self):
        self.reads = []

    def perceive(self, inputs):
        shared_memory = inputs.shared_memory
        self.reads.append((
            shared_memory.get(LEDGER_KEY),
            shared_memory.get("test.step"),
        ))
        shared_memory["test.perception"] = inputs.frame_id
        return PerceptionEvidenceBatch(signals=(PerceptionSignal("probe_seen", True),))


class WorkbenchTests(unittest.TestCase):
    def test_shared_memory_connects_plugin_and_memory_step_across_frames(self):
        mapper = PerceptionRunner.from_selection(
            plugins=["probe"],
            plugin_specs={"probe": f"{__name__}:SharedMemoryProbe"},
        )
        step_reads = []
        reports = []
        published = []

        def memory_factory():
            step = default_memory_step()

            class RecordingStep:
                def __call__(self, context, observation):
                    step_reads.append(context.shared_memory["test.perception"])
                    context.shared_memory["test.step"] = context.frame_id
                    report = step(context, observation)
                    reports.append(report)
                    published.append(context.shared_memory[LEDGER_KEY])
                    return report

                def reset(self):
                    return step.reset()

            return RecordingStep()

        with image_source(2) as root:
            runner = ImageReplayRunner(
                root, cadence_ms=0, mapper_factory=lambda: mapper,
                memory_step_factory=memory_factory,
            )
            runner.start()
            completed = runner.wait(5)
            self.assertEqual(completed["phase"], "completed")
            reads = mapper.plugins[0].reads
            self.assertEqual(reads[0], (None, None))
            self.assertIs(reads[1][0], published[0])
            self.assertEqual(reads[1][1], step_reads[0])
            self.assertEqual(completed["steps"]["memory"], last_plugin_state(reports[-1]))
            runner.start()
            self.assertEqual(runner.wait(5)["phase"], "completed")
            self.assertEqual(reads[2], (None, None))

    def test_pause_resume_step_reset_and_stale_run_are_server_owned(self) -> None:
        with image_source(3) as root:
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

    def test_sequential_replay_uses_shared_memory_without_legacy_handoff(self) -> None:
        with image_source(2) as root:
            write_manifest(root, {
                "camera_frames": [
                    {
                        "frame_id": "camera-first",
                        "frame_index": 10,
                        "captured_at_ms": 1000,
                        "image": "frame_00.png",
                    },
                    {
                        "frame_id": "camera-next",
                        "frame_index": 13,
                        "captured_at_ms": 1080,
                        "image": "frame_01.png",
                    },
                ]
            })
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
        self.assertIsInstance(mapper.priors[1], EvidenceLedger)
        self.assertEqual(mapper.legacy_handoff, [False, False])

    def test_seek_jumps_current_frame_and_reuses_processed_history(self) -> None:
        with image_source(4) as root:
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
            self.assertEqual(len(mapper.calls), calls_after_first + 2)
            self.assertEqual(
                sought["steps"]["decision"]["frame_id"],
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
            self.assertEqual(cached["steps"]["decision"]["frame_id"], first_id)
            self.assertEqual(len(mapper.calls), calls_after_first + 2)
            cached_next = runner.dispatch("step", run_id=run_id)
            self.assertEqual(cached_next["current_frame"]["position"], 1)
            self.assertEqual(len(mapper.calls), calls_after_first + 2)

            with self.assertRaises(ReplayActionError):
                runner.dispatch("seek", run_id=run_id, position=99)
            with self.assertRaises(ReplayActionError):
                runner.dispatch("seek", run_id=run_id)
            runner.dispatch("reset", run_id=run_id)

        idle = ImageReplayRunner()
        with self.assertRaises(ReplayActionError):
            idle.dispatch("seek", run_id="missing", position=0)

    def test_loop_playback_reprocesses_the_capture_each_pass(self) -> None:
        with image_source(2) as root:
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
            # Each pass runs both frames through the pipelines again.
            _wait_until(lambda: len(mapper.calls) >= 4)
            live = runner.state()
            self.assertEqual(live["phase"], "running")
            self.assertLessEqual(len(live["timeline"]), 2)
            runner.dispatch("set_loop", run_id=run_id, loop=False)
            finished = runner.wait(5)
            self.assertEqual(finished["phase"], "completed")
            self.assertFalse(finished["controls"]["loop"])

    def test_seek_while_running_pauses_and_serves_frame_bytes_by_position(self) -> None:
        with image_source(4) as root:
            runner = ImageReplayRunner(root, cadence_ms=5000)
            base = serve_workbench(self, runner)

            post = partial(post_action, base, timeout=10)

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
            post({"action": "reset", "run_id": run_id})

    def test_realtime_pace_honors_recorded_frame_timestamps(self) -> None:
        with image_source(3) as root:
            write_manifest(root, {
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
            })
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
