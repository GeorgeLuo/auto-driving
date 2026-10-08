from __future__ import annotations

import io
import json
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import numpy as np

from autonomy.decision_cycle.steps import decision_steps
from autonomy.runtime.session import RunConfiguration
from cli.automa_cli import onboard_automation
from implementations.runtime.picar import AutonomyPilotPart, create_host
from tests.integration.automation_pipeline.cadence_fixtures import CaptureClock, GatedPerception


def _part(perception: GatedPerception, clock: CaptureClock, interval_s: float) -> AutonomyPilotPart:
    host = create_host(steps=replace(decision_steps(), perception=perception))
    part = AutonomyPilotPart(host=host, interval_s=interval_s, monotonic=clock, run_id="cadence-test")
    host.register_status_provider("observation", part.observation_status)
    return part


class OnboardCaptureCadenceTests(unittest.TestCase):
    def test_unsampled_drive_ticks_do_not_become_captures_or_skips(self) -> None:
        clock = CaptureClock()
        perception = GatedPerception(2, blocked=())
        part = _part(perception, clock, 0.5)
        image = np.ones((2, 2, 3), dtype=np.uint8)
        try:
            part.start(RunConfiguration(mode="observe_only", interval_s=0.5))
            part.run(image_array=image, mode="user")
            part.wait_for_cycle()
            clock.advance(0.2)
            part.run(image_array=np.full_like(image, 2), mode="user")
            self.assertEqual(part.publish_latest_camera()["frame"]["frame_index"], 0)
            clock.advance(0.3)
            part.run(image_array=np.full_like(image, 3), mode="user")
            part.wait_for_cycle()
            self.assertEqual([c.metadata["capture_sequence"] for c in perception.contexts], [0, 1])
            status = part.observation_status()
            self.assertEqual((status["frames_captured"], status["processed_count"], status["skipped_count"]), (2, 2, 0))
            self.assertEqual(int(part.latest_state.image[0, 0, 0]), 3)
        finally:
            perception.release_all()
            part.wait_for_cycle()
            part.shutdown()

    def test_latest_capture_drains_without_another_drive_tick_and_publishes_skips(self) -> None:
        clock = CaptureClock()
        perception = GatedPerception(3, blocked=(0, 1))
        part = _part(perception, clock, 0.5)
        image = np.zeros((2, 2, 3), dtype=np.uint8)
        try:
            part.start(RunConfiguration(mode="observe_only", interval_s=0.5))
            part.run(image_array=image, mode="user")
            perception.wait_started(0)
            for value in (1, 2, 3):
                clock.advance(0.5)
                part.run(image_array=np.full_like(image, value), mode="user")
            self.assertEqual(part.publish_latest_camera()["frame"]["frame_index"], 3)
            self.assertEqual(part.publish_latest_camera()["perception_state"], "pending")
            perception.release[0].set()
            # Capture time does not advance and no new drive tick wakes this cycle.
            perception.wait_started(1)
            for value in (4, 5):
                clock.advance(0.5)
                part.run(image_array=np.full_like(image, value), mode="user")
            perception.release[1].set()
            part.wait_for_cycle()
            self.assertEqual([c.metadata["capture_sequence"] for c in perception.contexts], [0, 3, 5])
            self.assertEqual([c.metadata["skipped_since_previous"] for c in perception.contexts], [0, 2, 1])
            publication = part.publish_latest()
            self.assertEqual((publication["frames_captured"], publication["processed_count"], publication["skipped_count"]), (6, 3, 3))
            self.assertEqual(publication["skipped_since_previous"], 1)
            self.assertEqual(publication["frame"]["frame_index"], 5)
            self.assertEqual(int(part.latest_state.image[0, 0, 0]), 5)
        finally:
            perception.release_all()
            part.wait_for_cycle()
            part.shutdown()

    def test_stop_and_bounded_completion_exclude_pending_captures_from_skips(self) -> None:
        for bounded in (False, True):
            with self.subTest(bounded=bounded):
                perception = GatedPerception(1)
                part = _part(perception, CaptureClock(), 0.0)
                try:
                    part.start(RunConfiguration(mode="observe_only", interval_s=0.0, num_decisions=int(bounded)))
                    image = np.zeros((2, 2, 3), dtype=np.uint8)
                    part.run(image_array=image, mode="user")
                    perception.wait_started(0)
                    part.run(image_array=image, mode="user")
                    part.run(image_array=image, mode="user")
                    if not bounded:
                        part.stop()
                    perception.release[0].set()
                    part.wait_for_cycle()
                    self.assertEqual(len(perception.contexts), 1)
                    status = part.observation_status()
                    self.assertEqual((status["frames_captured"], status["skipped_count"]), (3, 0))
                    self.assertEqual(part.publish_latest()["skipped_since_previous"], 0)
                    self.assertEqual(part.host.run_state, "completed" if bounded else "stopped")
                finally:
                    perception.release_all()
                    part.wait_for_cycle()
                    part.shutdown()

    def test_monitor_reads_real_publications_independently_of_capture_interval(self) -> None:
        clock = CaptureClock()
        perception = GatedPerception(3, blocked=(1,))
        part = _part(perception, clock, 10.0)
        image = np.zeros((2, 2, 3), dtype=np.uint8)

        class RuntimeClient:
            polls = 0

            def start(self, configuration, *, record=False):
                if record:
                    part.recording_root = root / "host-runs"
                    part.vehicle_id = "picar-test"
                    part.recording_root.mkdir(parents=True, exist_ok=True)
                # Frame 3 is the third decision. A bound of 3 would end the
                # session inside that poll, and a recorded terminal session is
                # drained before the monitor reads the live publication.
                status = part.start(replace(configuration, num_decisions=0), record=record)
                part.run(image_array=image, mode="user")
                part.wait_for_cycle()
                return {"ok": True, "host_run_id": part.run_id, "session": status}

            def read_recording(self, run_id, *, after):
                return part.host.recording.read(run_id=run_id, after=after)

            def stop(self):
                return {"ok": True, "session": part.stop()}

            def status(self):
                self.polls += 1
                if self.polls == 2:
                    clock.advance(10.0)
                    part.run(image_array=np.full_like(image, 1), mode="user")
                    perception.wait_started(1)
                    for value in (2, 3):
                        clock.advance(10.0)
                        part.run(image_array=np.full_like(image, value), mode="user")
                    perception.release[1].set()
                    part.wait_for_cycle()
                elif self.polls >= 3 and part.host.run_state == "running":
                    part.host.stop(reason="completed")
                return {"ok": True, "host_run_id": part.run_id, "session": part.host.session_status()}

        def frame(*_args, **_kwargs):
            jpeg, publication = part.publish_latest_frame_jpeg()
            return jpeg, {"x-frame-id": publication["frame"]["frame_id"], "content-type": "image/jpeg"}

        seen = []

        def publication(*_args, **_kwargs):
            payload = part.publish_latest()
            seen.append(payload["frame"]["frame_index"])
            return payload

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = io.StringIO()
            try:
                # Substitute HTTP reads and lifecycle transport only. Host, workers,
                # publications, monitor polling, loopback view and records are real.
                with (
                    patch.object(onboard_automation, "OnboardRuntimeClient", return_value=RuntimeClient()),
                    patch.object(onboard_automation, "fetch_autonomy_status", side_effect=lambda *_a, **_kw: {"autonomy": part.host.status()}),
                    patch.object(onboard_automation, "fetch_observation_publication", side_effect=publication),
                    patch.object(onboard_automation, "fetch_observation_frame", side_effect=frame),
                    patch("cli.automa_cli.decision_live.fetch_observation_frame", side_effect=frame),
                    patch("cli.automa_cli.decision_live.fetch_decision_publication", side_effect=lambda *_a, **_kw: part.publish_decision_latest()),
                ):
                    started = time.monotonic()
                    code, message = onboard_automation.monitor_onboard_runtime(
                        vehicle_id="picar-test", base_url="http://picar.invalid", automation_dir=root,
                        perception={"preset": "gated", "plugins": [], "activation": "perception/active.json"},
                        decision={"generation_id": "test", "steps": {}, "published": False},
                        step_activations={}, configuration=RunConfiguration(mode="observe_only", interval_s=10.0, num_decisions=3),
                        timeout_s=0.2, record=True, verbose=True, output=output,
                    )
                self.assertEqual(code, 0, message)
                self.assertLess(time.monotonic() - started, 2.0, "monitor waited for capture cadence")
                state = json.loads((root / "state.json").read_text())
                self.assertEqual(seen, [0, 3])
                self.assertEqual((state["frames_captured"], state["processed_count"], state["skipped_count"]), (4, 3, 1))
                self.assertEqual(state["num_decisions"], 3)
                self.assertEqual(state["session"]["configuration"]["interval_s"], 10.0)
                self.assertEqual(state["last_frame"]["skipped_since_previous"], 1)
                recording_id = state["session"]["recording"]["run_id"]
                records = [json.loads(p.read_text()) for p in (root / "runs" / recording_id).glob("perception/*/perception.json")]
                records.sort(key=lambda record: record["frame_index"])
                # Live samples are the latest publication at each poll, so frame 1
                # is absent from `seen`. The recording keeps that completed decision.
                self.assertEqual([record["frame_index"] for record in records], [0, 1, 3])
                self.assertEqual([record["skipped_since_previous"] for record in records], [0, 0, 1])
                self.assertIn("skipped_since_previous=1", output.getvalue())
                self.assertIn("Frames superseded before decision: 1", message)
            finally:
                perception.release_all()
                part.wait_for_cycle()
                part.shutdown()
