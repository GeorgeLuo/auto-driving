"""Live and replay viewers read the same exported cycle body."""

from __future__ import annotations

import io
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from PIL import Image

from autonomy.decision_cycle.cycle import CYCLE_RECORD_KEYS, DecisionCycle, DecisionSteps
from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.perception.runner import PerceptionRunner
from autonomy.runtime.control import AutonomyControl
from autonomy.runtime.execution import ControlApplication
from autonomy.runtime.report import report_from_host_result
from cli.automa_cli.frontend.decision_view import DecisionView
from cli.automa_cli.plugins.inspect.replay import read_replay_source
from cli.automa_cli.replay.frames import run_frame
from cli.automa_cli.replay.selections import default_selection
from cli.automa_cli.workbench.page_payloads import displayed_steps
from tests.shared.cli.memory.frame_fixtures import write_frames
from tests.shared.cli.workbench_fixtures import (
    ErrorMemory,
    FixtureMapper,
    ImageReplayRunner,
    _wait_until,
    image_source,
)
from tests.shared.integration.plugin_flow_fixtures import frame
from tests.support.action_fixtures import fixed_action_runner, plan_runner, proposal_runner


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), (12, 24, 36)).save(buffer, format="PNG")
    return buffer.getvalue()


class ViewerCycleAgreementTests(unittest.TestCase):
    def test_replay_steps_match_the_exported_cycle_including_nulls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = write_frames(Path(directory) / "source", count=1)
            images = read_replay_source(source, max_frames=8)
            replay_frame = replace(images.frames[0], frame_id="frame-1", frame_index=1)
            mapper = PerceptionRunner.from_activation(default_selection("perception"))
            result = run_frame(
                replay_frame, steps={"perception": mapper}, shared_memory={},
            ).result

            steps = displayed_steps(result)
            self.assertEqual(set(steps), {"perception", *CYCLE_RECORD_KEYS})
            self.assertNotIn("decision", steps)
            self.assertEqual(
                {key: steps[key] for key in CYCLE_RECORD_KEYS},
                result.export_cycle(),
            )
            self.assertEqual(steps["perception"], result.perception.to_dict())
            self.assertIsNotNone(steps["observation"])
            self.assertIsNone(steps["proposal"])
            self.assertIsNone(steps["plan"])
            self.assertIsNone(steps["action"])

            result.observation.metadata["injected"] = "source"
            steps["observation"]["metadata"]["injected"] = "caller"
            retained = displayed_steps(result)
            self.assertNotIn("injected", retained["observation"]["metadata"])

            absent = run_frame(
                replace(
                    replay_frame, frame_id="gap", frame_index=2, image_path=None,
                    absence_reason="camera missing",
                ),
                steps={"perception": mapper},
                shared_memory={},
            ).result
            absent_steps = displayed_steps(absent)
            self.assertIsNone(absent_steps["perception"])
            self.assertIsNone(absent.perception)
            self.assertIsNotNone(absent_steps["observation"])
            self.assertEqual(absent_steps["observation"]["observation_id"], absent.observation.observation_id)
            self.assertIsNone(absent_steps["action"])

    def test_live_payload_uses_the_report_cycle_without_synthesized_perception(self) -> None:
        with image_source(1) as root:
            context = frame(root, 1)
            observation = Observation(
                context.frame_id,
                context.timestamp_ms,
                {},
                summary=("ahead",),
                things=({"thing_id": "one", "kind": "obstacle"},),
                signals=({"name": "range"},),
                metadata={"labels": ["first"]},
            )
            result = DecisionCycle(DecisionSteps(
                observation=lambda _context, _perception: observation,
                memory=lambda _context: None,
                proposal=proposal_runner({}),
                plan=plan_runner(),
                action=fixed_action_runner(AutonomyControl(steering=0.25)),
            )).run(context)
            result = replace(result, application=ControlApplication(
                context.frame_id, "observe_only", False, "observe-only", result.control,
            ))
            published_at_ms = context.timestamp_ms + 40
            report = report_from_host_result(
                result,
                host_name="fixture",
                run_id="run",
                generation_id="generation",
                published_at_ms=published_at_ms,
                values={"source_id": "runtime-host", "stale_after_ms": 5000},
            )
            view = DecisionView(host_name="fixture", identity={
                "host_name": "fixture",
                "source_id": "runtime-host",
                "run_id": "run",
                "producer_generation_id": "generation",
            })
            self.assertTrue(view.publish(
                report=report,
                image=(_png(), "image/png"),
                host_telemetry={"status": "unavailable", "reason": "publisher_missing"},
            ))
            report.cycle["observation"]["metadata"]["labels"].append("source")

            payload = view.latest_payload(
                generation=view.generation_id, now_ms=published_at_ms + 10,
            )
            cycle = payload["decision"]["cycle"]
            self.assertEqual(set(cycle), set(CYCLE_RECORD_KEYS))
            self.assertIsNone(cycle["memory"])
            self.assertEqual(cycle["observation"]["metadata"]["labels"], ["first"])
            self.assertEqual(cycle["observation"]["things"][0]["thing_id"], "one")
            self.assertNotIn("perception", payload)
            self.assertNotIn("provenance", payload)
            self.assertEqual(payload["current_image"]["frame_id"], payload["decision"]["frame_id"])
            self.assertEqual(payload["freshness"]["captured_at_ms"], payload["decision"]["timestamp_ms"])
            self.assertNotEqual(
                payload["freshness"]["captured_at_ms"],
                payload["freshness"]["published_at_ms"],
            )
            self.assertEqual(payload["authority"], cycle["action"]["authority"])
            self.assertFalse(payload["decision"]["application"]["applied"])
            self.assertEqual(payload["authority"]["authorized_output"]["steering"], 0.25)
            self.assertEqual(payload["host_telemetry"]["reason"], "publisher_missing")

            payload["decision"]["cycle"]["observation"]["metadata"]["labels"].append("caller")
            payload["authority"]["authorized_output"]["steering"] = 1
            again = view.latest_payload(
                generation=view.generation_id, now_ms=published_at_ms + 10,
            )
            self.assertEqual(again["decision"]["cycle"]["observation"]["metadata"]["labels"], ["first"])
            self.assertEqual(again["authority"]["authorized_output"]["steering"], 0.25)

    def test_workbench_timing_follows_the_displayed_frame_and_clears(self) -> None:
        with image_source(4) as root:
            runner = ImageReplayRunner(
                root, cadence_ms=5000, step_factories={"perception": FixtureMapper},
            )
            try:
                run_id = runner.start()["run_id"]
                _wait_until(lambda: runner.state()["progress"]["completed"] >= 1)
                paused = runner.dispatch("pause", run_id=run_id)
                later = runner.dispatch("seek", run_id=run_id, position=2)
                self.assertEqual(
                    later["steps"]["action"]["frame_id"], later["current_frame"]["frame_id"],
                )
                self.assertEqual(later["timing"], runner.state()["timing"])
                restored = runner.dispatch("seek", run_id=run_id, position=0)
                self.assertEqual(restored["timing"], paused["timing"])
                self.assertEqual(restored["steps"]["action"]["frame_id"], paused["current_frame"]["frame_id"])
                returned = runner.dispatch("seek", run_id=run_id, position=2)
                self.assertEqual(returned["timing"], later["timing"])
                cleared = runner.dispatch("reset", run_id=run_id)
                self.assertIsNone(cleared["timing"])
                self.assertIsNone(cleared["steps"]["action"])
            finally:
                runner.close()

        with image_source(1) as root:
            failed = ImageReplayRunner(
                root,
                cadence_ms=0,
                step_factories={"perception": FixtureMapper, "memory": lambda: ErrorMemory()},
            )
            started = failed.start()
            state = failed.wait(5) if started["phase"] == "running" else started
            self.assertEqual(state["phase"], "failed")
            self.assertIsNone(state["timing"])
            self.assertIsNone(state["steps"]["perception"])
            self.assertIsNone(state["steps"]["action"])

    def test_returned_workbench_state_does_not_own_the_retained_cycle(self) -> None:
        with image_source(1) as root:
            runner = ImageReplayRunner(
                root, cadence_ms=0, step_factories={"perception": FixtureMapper},
            )
            runner.start()
            state = runner.wait(5)
            applied = state["steps"]["action"]["authority"]["proposed_applied"]
            state["steps"]["memory"]["plugins"][0]["state"]["record_count"] = 999
            state["steps"]["action"]["authority"]["proposed_applied"] = not applied
            state["steps"]["observation"]["metadata"]["injected"] = True
            retained = runner.state()
            self.assertNotEqual(
                retained["steps"]["memory"]["plugins"][0]["state"]["record_count"], 999,
            )
            self.assertEqual(retained["steps"]["action"]["authority"]["proposed_applied"], applied)
            self.assertNotIn("injected", retained["steps"]["observation"]["metadata"])


if __name__ == "__main__":
    unittest.main()
