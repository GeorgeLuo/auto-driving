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
from cli.automa_cli import runtime_monitor
from implementations.runtime.picar import AutonomyPilotPart, create_host
from tests.deterministic.cli.automation_pipeline.cadence_fixtures import GatedPerception


def _part(perception: GatedPerception) -> AutonomyPilotPart:
    host = create_host(steps=replace(decision_steps(), perception=perception))
    return AutonomyPilotPart(host=host, run_id="cadence-test")


class OnboardCaptureCadenceTests(unittest.TestCase):
    def test_due_is_false_while_a_decision_or_frame_is_held(self) -> None:
        perception = GatedPerception(2)
        part = _part(perception)
        image = np.zeros((2, 2, 3), dtype=np.uint8)
        try:
            part.start(RunConfiguration(mode="observe_only"))
            self.assertTrue(part.due())
            part.run(image_array=image, mode="user")
            perception.wait_started(0)
            self.assertTrue(part._decision_inflight)
            self.assertIsNone(part._pending_frame)
            self.assertFalse(part.due())
            part.run(image_array=np.full_like(image, 1), mode="user")
            self.assertEqual(part.observation_status()["frames_captured"], 1)
            self.assertFalse(part.due())
            perception.release[0].set()
            part.wait_for_cycle()
            self.assertFalse(part._decision_inflight)
            self.assertIsNone(part._pending_frame)
            self.assertTrue(part.due())
            part.run(image_array=np.full_like(image, 2), mode="user")
            part.wait_for_cycle()
            self.assertTrue(part.due())
            status = part.observation_status()
            self.assertEqual(
                (status["frames_captured"], status["processed_count"], status["skipped_count"]),
                (2, 2, 0),
            )
        finally:
            perception.release_all()
            part.wait_for_cycle()
            part.shutdown()

        held = _part(GatedPerception(1, blocked=()))
        try:
            held.start(RunConfiguration(mode="observe_only"))
            with held._lock:
                held._memory_update_halted = True
            held.submit(image, mode="observe_only")
            self.assertIsNotNone(held._pending_frame)
            self.assertFalse(held._decision_inflight)
            self.assertFalse(held.due())
            with held._lock:
                held._pending_frame = None
                held._memory_update_halted = False
            self.assertTrue(held.due())
        finally:
            held.shutdown()

    def test_stop_and_bounded_completion_exclude_pending_captures_from_skips(self) -> None:
        for bounded in (False, True):
            with self.subTest(bounded=bounded):
                perception = GatedPerception(1)
                part = _part(perception)
                try:
                    part.start(RunConfiguration(mode="observe_only", num_decisions=int(bounded)))
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
                    self.assertEqual((status["frames_captured"], status["skipped_count"]), (1, 0))
                    self.assertEqual(part.publish_latest()["skipped_since_previous"], 0)
                    self.assertEqual(part.host.run_state, "completed" if bounded else "stopped")
                finally:
                    perception.release_all()
                    part.wait_for_cycle()
                    part.shutdown()

    def test_monitor_reads_publications_as_soon_as_the_worker_is_free(self) -> None:
        perception = GatedPerception(3, blocked=())
        part = _part(perception)
        image = np.zeros((2, 2, 3), dtype=np.uint8)

        class RuntimeClient:
            base_url = "http://picar.invalid"
            polls = 0

            def host_status(self):
                return {"autonomy": part.host.status()}

            def start(self, configuration, *, record=False):
                if record:
                    part.recording_root = root / "host-runs"
                    part.vehicle_id = "picar-test"
                    part.recording_root.mkdir(parents=True, exist_ok=True)
                # A bound of 3 would end the session inside the start, and a
                # recorded terminal session is drained before the monitor reads
                # the live publication.
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
                    part.run(image_array=np.full_like(image, 1), mode="user")
                    part.wait_for_cycle()
                    part.run(image_array=np.full_like(image, 2), mode="user")
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
                    patch.object(runtime_monitor, "RuntimeClient", return_value=RuntimeClient()),
                    patch.object(runtime_monitor, "fetch_observation_publication", side_effect=publication),
                    patch.object(runtime_monitor, "fetch_observation_frame", side_effect=frame),
                    patch("cli.automa_cli.decision_live.fetch_observation_frame", side_effect=frame),
                    patch("cli.automa_cli.decision_live.fetch_decision_publication", side_effect=lambda *_a, **_kw: part.publish_decision_latest()),
                ):
                    started = time.monotonic()
                    code, message = runtime_monitor.monitor_runtime(
                        vehicle_id="picar-test", base_url="http://picar.invalid", automation_dir=root,
                        selections={"perception": {"preset": "gated", "plugins": [], "activation": "perception/active.json"}},
                        decision={"generation_id": "test", "steps": {}, "published": False},
                        configuration=RunConfiguration(mode="observe_only", num_decisions=3),
                        timeout_s=0.2, record=True, verbose=True, output=output,
                    )
                self.assertEqual(code, 0, message)
                self.assertLess(time.monotonic() - started, 2.0, "monitor waited after the worker was free")
                state = json.loads((root / "state.json").read_text())
                self.assertEqual(seen, [0, 2])
                self.assertEqual((state["frames_captured"], state["processed_count"], state["skipped_count"]), (3, 3, 0))
                self.assertEqual(state["num_decisions"], 3)
                self.assertNotIn("interval_s", state["session"]["configuration"])
                self.assertEqual(state["last_frame"]["skipped_since_previous"], 0)
                recording_id = state["session"]["recording"]["run_id"]
                records = [json.loads(p.read_text()) for p in (root / "runs" / recording_id).glob("perception/*/perception.json")]
                records.sort(key=lambda record: record["frame_index"])
                # Live samples are the latest publication at each poll, so frame 1
                # is absent from `seen`. The recording keeps that completed decision.
                self.assertEqual([record["frame_index"] for record in records], [0, 1, 2])
                self.assertEqual([record["skipped_since_previous"] for record in records], [0, 0, 0])
                self.assertIn("Frames superseded before decision: 0", message)
            finally:
                perception.release_all()
                part.wait_for_cycle()
                part.shutdown()
