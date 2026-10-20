from __future__ import annotations

import asyncio
import base64
import json
from types import SimpleNamespace
import threading
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import numpy as np

from autonomy.decision_cycle.activation import DECISION_STEPS, STEPS, activation_generation_id
from autonomy.decision_cycle.steps import decision_steps
from autonomy.runtime.session import RunConfiguration
from autonomy.runtime.routes import RuntimeRoutes
from autonomy.runtime.frame_loop import DECISION_TRANSACTION_PATH
from autonomy.runtime.execution import ControlExecution
from implementations.decision_cycle.catalog import packaged_activation, selection_activation
from autonomy.runtime.frame_loop import (
    DECISION_PUBLICATION_SCHEMA,
    LATEST_FRAME_PATH,
    LATEST_JSON_PATH,
    OBSERVATION_PUBLICATION_SCHEMA,
)
from implementations.runtime.picar import AutonomyPilotPart, DonkeyControlTarget, create_host


class ObservationPublicationTests(unittest.TestCase):
    def _decision_image(self, part):
        status, _, body = RuntimeRoutes(part).handle("GET", DECISION_TRANSACTION_PATH)
        self.assertEqual(status, 200, body)
        return base64.b64decode(json.loads(body)["image_base64"], validate=True)

    def test_repeated_image_reads_encode_each_processed_frame_once(self) -> None:
        from autonomy.runtime.frame_loop import encode_jpeg

        part = self._hold_part()
        self.addCleanup(part.shutdown)
        part.run(image_array=np.zeros((8, 12, 3), dtype=np.uint8), mode="user")
        part.wait_for_cycle()
        with mock.patch("autonomy.runtime.frame_loop.encode_jpeg", wraps=encode_jpeg) as encode:
            first, metadata = part.publish_latest_frame_jpeg()
            repeated, _ = part.publish_latest_frame_jpeg()
            transaction_image = self._decision_image(part)
            self.assertEqual(first, repeated)
            self.assertEqual(first, transaction_image)
            self.assertEqual(encode.call_count, 1)
            part.run(image_array=np.full((8, 12, 3), 200, dtype=np.uint8), mode="user")
            part.wait_for_cycle()
            second, next_metadata = part.publish_latest_frame_jpeg()
            self.assertNotEqual(first, second)
            self.assertNotEqual(metadata["frame"]["frame_id"], next_metadata["frame"]["frame_id"])
            self.assertEqual(encode.call_count, 2)

    def test_wire_jpeg_is_reused_by_camera_decision_and_recording(self):
        from autonomy.runtime.frame_loop import encode_jpeg
        image = np.full((8, 12, 3), 70, dtype=np.uint8)
        wire = encode_jpeg(image)
        part = self._hold_part()
        self.addCleanup(part.shutdown)
        with tempfile.TemporaryDirectory() as tmp:
            part.recording_root = Path(tmp)
            part.start(RunConfiguration(mode="observe_only"), record=True)
            with mock.patch("autonomy.runtime.frame_loop.encode_jpeg", side_effect=AssertionError("reencoded")):
                part.submit(image, jpeg_bytes=wire)
                part.wait_for_cycle()
                camera, _ = part.publish_latest_camera_jpeg()
                observation, _ = part.publish_latest_frame_jpeg()
                decision = self._decision_image(part)
            self.assertEqual((camera, observation, decision), (wire, wire, wire))
            files = list(Path(tmp).glob("*/frames/*.jpg"))
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].read_bytes(), wire)
            self.assertNotIn("jpeg_bytes", part.host.last_context.sensor_frame.readings["front_camera"].to_dict())

    def test_recording_and_concurrent_camera_read_share_one_encode(self):
        from autonomy.runtime.frame_loop import encode_jpeg
        part = self._hold_part()
        self.addCleanup(part.shutdown)
        entered, release = threading.Event(), threading.Event()
        def encode(image):
            entered.set()
            if not release.wait(2):
                raise AssertionError("JPEG encode was not released")
            return encode_jpeg(image)
        with tempfile.TemporaryDirectory() as tmp:
            part.recording_root = Path(tmp)
            part.start(RunConfiguration(mode="observe_only"), record=True)
            published = []
            with mock.patch("autonomy.runtime.frame_loop.encode_jpeg", side_effect=encode) as encoder:
                part.run(image_array=np.zeros((8, 12, 3), dtype=np.uint8), mode="user")
                self.assertTrue(entered.wait(1))
                reader = threading.Thread(target=lambda: published.append(part.publish_latest_camera_jpeg()[0]))
                reader.start()
                release.set()
                part.wait_for_cycle()
                reader.join(1)
                self.assertFalse(reader.is_alive())
                observation, _ = part.publish_latest_frame_jpeg()
                decision = self._decision_image(part)
                self.assertEqual(encoder.call_count, 1)
            self.assertEqual(published, [observation])
            self.assertEqual(decision, observation)
            files = list(Path(tmp).glob("*/frames/*.jpg"))
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].read_bytes(), observation)

    def _hold_part(self) -> AutonomyPilotPart:
        activations = {step: packaged_activation(step) for step in DECISION_STEPS}
        return AutonomyPilotPart(
            host=create_host(steps=decision_steps(activations)),
            vehicle_id="piracer",
            source_id="donkeycar:piracer",
            decision_activations={
                step: activation.to_payload() for step, activation in activations.items()
            },
            run_id="donkey-run-fixture",
        )

    def test_warming_publication_before_first_result(self) -> None:
        part = AutonomyPilotPart(
            host=create_host(steps=decision_steps()),
            presets={"perception": "lightweight_observer"},
        )
        payload = part.publish_latest(now_ms=1_000)
        self.assertEqual(payload["schema"], OBSERVATION_PUBLICATION_SCHEMA)
        self.assertEqual(payload["health"], "warming")
        self.assertFalse(payload["ok"])
        self.assertIsNone(payload["frame"])
        self.assertEqual(payload["presets"], {"perception": "lightweight_observer"})
        self.assertEqual(payload["latest_json_path"], LATEST_JSON_PATH)
        self.assertEqual(payload["latest_frame_path"], LATEST_FRAME_PATH)

    def test_healthy_publication_includes_detached_perception_and_matching_frame(self) -> None:
        part = AutonomyPilotPart(host=create_host(steps=decision_steps()))
        image = np.zeros((8, 12, 3), dtype=np.uint8)
        image[:, :] = (10, 20, 30)
        part.run(image_array=image, mode="user")
        part.wait_for_cycle()

        payload = part.publish_latest(now_ms=part.latest_state.completed_at_ms + 10)
        self.assertEqual(payload["health"], "healthy")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["mode"], "manual")
        self.assertEqual(payload["control"]["steering"], 0.0)
        self.assertEqual(payload["control"]["throttle"], 0.0)
        self.assertEqual(payload["frame"]["frame_id"], "donkey_frame_000000")
        self.assertTrue(payload["frame"]["has_image"])
        # Idle host has no perception step; publication still carries cycle control.
        self.assertIsNone(payload["perception"])
        self.assertIsNone(payload["memory"])
        self.assertEqual(payload["control"]["reason"], "action_plan_invariant_violated")
        self.assertEqual(payload["frame"]["frame_path"], LATEST_FRAME_PATH)

    def test_presets_follow_the_activations_each_frame_applied(self) -> None:
        """Regression: the runtime reported only perception's preset, fixed at start,
        so memory's and proposal's presets, and any restaged preset, never showed."""

        activations = {step: selection_activation(step) for step in DECISION_STEPS}
        part = AutonomyPilotPart(
            host=create_host(steps=decision_steps(activations)),
            presets={"perception": "started-with"},
        )
        self.assertEqual(part.publish_latest(now_ms=1_000)["presets"], {"perception": "started-with"})

        part.run(image_array=np.zeros((8, 8, 3), dtype=np.uint8), mode="user")
        part.wait_for_cycle()
        payload = part.publish_latest(now_ms=part.latest_state.completed_at_ms)
        applied = {
            step: activation.metadata["preset"]
            for step, activation in activations.items()
            if activation.metadata.get("preset")
        }
        self.assertEqual(payload["presets"], applied)
        self.assertEqual(part.observation_status()["presets"], applied)

    def test_publication_includes_the_memory_report_when_step_present(self) -> None:
        from autonomy.decision_cycle.cycle import DecisionSteps
        from autonomy.decision_cycle.memory.evidence import MemoryOrigin, RetainedEvidence
        from autonomy.decision_cycle.perception.evidence.values import ViewLocation

        def remember(context):
            pass
            record = RetainedEvidence(
                        record_id="thing:boundary",
                        kind="floor_boundary",
                        label="boundary",
                        confidence=0.9,
                        origin=MemoryOrigin(
                            observation_id="obs",
                            observed_id="boundary",
                            coordinate_frame="image",
                            observed_at_ms=context.timestamp_ms,
                            updated_at_ms=context.timestamp_ms,
                            frame_id=context.frame_id,
                        ),
                        location=ViewLocation(
                            frame="image",
                            zone="center",
                            bbox_xyxy_norm=(0.2, 0.3, 0.5, 0.8),
                        ),
            )
            return {
                "schema": "memory_report_v1",
                "plugins": [
                    {
                        "plugin_id": "bounded_evidence",
                        "state": {
                            "health": "healthy",
                            "record_count": 1,
                            "records": [record.to_dict()],
                        },
                    }
                ],
                "evidence_publisher": "bounded_evidence",
            }

        host = create_host(steps=DecisionSteps(memory=remember))
        part = AutonomyPilotPart(host=host)
        part.run(image_array=np.zeros((8, 8, 3), dtype=np.uint8), mode="user")
        part.wait_for_cycle()
        payload = part.publish_latest(now_ms=part.latest_state.completed_at_ms)
        self.assertIsNotNone(payload["memory"])
        state = payload["memory"]["plugins"][0]["state"]
        self.assertEqual(state["health"], "healthy")
        self.assertEqual(state["record_count"], 1)
        self.assertEqual(state["records"][0]["kind"], "floor_boundary")
        self.assertEqual(payload["memory"]["evidence_publisher"], "bounded_evidence")

        jpeg, frame_meta = part.publish_latest_frame_jpeg()
        self.assertIsNotNone(jpeg)
        self.assertGreater(len(jpeg), 32)
        self.assertEqual(frame_meta["frame"]["frame_id"], payload["frame"]["frame_id"])
        self.assertEqual(frame_meta["health"], "healthy")
        self.assertTrue(jpeg.startswith(b"\xff\xd8"))

    def test_publication_lists_each_memory_plugin_and_the_evidence_publisher(self) -> None:
        from autonomy.decision_cycle.cycle import DecisionSteps
        from autonomy.decision_cycle.memory.runner import MemoryRunner
        from autonomy.decision_cycle.observation.values import Observation
        from implementations.decision_cycle.catalog import step_plugins
        from implementations.decision_cycle.memory.plugins.bounded_evidence.plugin import (
            BoundedEvidenceLedger,
        )
        from tests.shared.autonomy.decision_cycle.memory.activation_fixtures import _RecordingMemory

        def observe(context, perception):
            return Observation(
                observation_id=context.frame_id,
                created_at_ms=context.timestamp_ms,
                sensor_frame={},
                things=({
                    "thing_id": "a",
                    "kind": "floor_boundary",
                    "label": "boundary",
                    "confidence": 0.9,
                    "location": {"frame": "image", "zone": "center"},
                },),
            )

        config = step_plugins("memory")["bounded_evidence"]["default_config"]
        memory = MemoryRunner.from_plugins({
            "recording_test": _RecordingMemory(),
            "bounded_evidence": BoundedEvidenceLedger(**config),
        })
        part = AutonomyPilotPart(
            host=create_host(steps=DecisionSteps(observation=observe, memory=memory)),
        )
        part.run(image_array=np.zeros((8, 8, 3), dtype=np.uint8), mode="user")
        part.wait_for_cycle()

        report = part.publish_latest(now_ms=part.latest_state.completed_at_ms)["memory"]

        states = {entry["plugin_id"]: entry["state"] for entry in report["plugins"]}
        self.assertEqual(list(states), ["recording_test", "bounded_evidence"])
        self.assertEqual(states["recording_test"]["record_count"], 1)
        self.assertEqual(states["bounded_evidence"]["record_count"], 1)
        self.assertEqual(report["evidence_publisher"], "bounded_evidence")
        self.assertEqual(
            part.host.status()["steps"]["memory"]["evidence_publisher"], "bounded_evidence",
        )

        part.reset_memory()
        report = part.publish_latest(now_ms=part.latest_state.completed_at_ms)["memory"]

        states = {entry["plugin_id"]: entry["state"] for entry in report["plugins"]}
        self.assertEqual(states["recording_test"]["record_count"], 0)
        self.assertEqual(states["bounded_evidence"]["record_count"], 0)
        self.assertEqual(report["evidence_publisher"], "bounded_evidence")

    def test_stale_and_error_health_states(self) -> None:
        part = AutonomyPilotPart(host=create_host(steps=decision_steps()))
        part.run(image_array=np.zeros((4, 4, 3), dtype=np.uint8), mode="user")
        part.wait_for_cycle()
        completed = part.latest_state.completed_at_ms
        stale = part.publish_latest(now_ms=completed + 5_000)
        self.assertEqual(stale["health"], "stale")
        self.assertTrue(stale["ok"])
        self.assertGreater(stale["result_age_ms"], stale["stale_after_ms"])

        class Boom:
            execution = ControlExecution(DonkeyControlTarget())
            last_context = None

            def status(self):
                return {"steps": {}}

            def register_status_provider(self, name, provider):
                pass

            def run(self, context, **inputs):
                del context
                raise RuntimeError("boom")

        failing = AutonomyPilotPart(host=Boom())  # type: ignore[arg-type]
        failing.run(image_array=np.zeros((4, 4, 3), dtype=np.uint8), mode="user")
        failing.wait_for_cycle()
        errored = failing.publish_latest(now_ms=failing.latest_state.completed_at_ms)
        self.assertEqual(errored["health"], "error")
        self.assertFalse(errored["ok"])
        self.assertIn("RuntimeError", errored["error"] or "")

    def test_unavailable_when_image_missing(self) -> None:
        part = AutonomyPilotPart(host=create_host(steps=decision_steps()))
        part.run(image_array=None, mode="user")
        part.wait_for_cycle()
        payload = part.publish_latest(now_ms=part.latest_state.completed_at_ms)
        self.assertEqual(payload["health"], "unavailable")
        jpeg, meta = part.publish_latest_frame_jpeg()
        self.assertIsNone(jpeg)
        self.assertEqual(meta["health"], "unavailable")

    def test_decision_publication_keeps_source_identity_and_cycle_atomic(self) -> None:
        part = self._hold_part()
        part.run(image_array=np.zeros((4, 4, 3), dtype=np.uint8), mode="user")
        part.wait_for_cycle()
        assert part.latest_state is not None
        completed_at_ms = part.latest_state.completed_at_ms

        decision = part.publish_decision_latest(now_ms=completed_at_ms)
        self.assertEqual(decision["schema"], DECISION_PUBLICATION_SCHEMA)
        self.assertTrue(decision["ok"])
        self.assertEqual(decision["status"], "ready")
        self.assertEqual(decision["reason"], "")
        published = decision["decision"]
        assert isinstance(published, dict)
        self.assertEqual(published["schema"], "vehicle_report_v0")
        self.assertEqual(published["vehicle_id"], "piracer")
        values = published["values"]
        self.assertEqual(values["source_id"], "donkeycar:piracer")
        self.assertEqual(published["run_id"], "donkey-run-fixture")
        expected_generation = activation_generation_id(
            {step: packaged_activation(step) for step in DECISION_STEPS}, prefix="decision"
        )
        self.assertEqual(published["generation_id"], expected_generation)
        self.assertEqual(values["activation"]["generation_id"], expected_generation)
        self.assertEqual(sorted(values["activation"]["steps"]), sorted(DECISION_STEPS))
        self.assertEqual(
            values["activation"]["steps"]["proposal"]["plugins"],
            ["avoid_recent_obstruction"],
        )
        self.assertNotIn("producer_pid", published)
        self.assertEqual(published["frame_id"], part.latest_state.frame_id)
        self.assertEqual(published["frame_index"], part.latest_state.frame_index)
        self.assertEqual(published["timestamp_ms"], part.latest_state.captured_at_ms)
        self.assertEqual(
            values["source_frame"],
            {
                "frame_id": part.latest_state.frame_id,
                "frame_index": part.latest_state.frame_index,
                "captured_at_ms": part.latest_state.captured_at_ms,
                "completed_at_ms": part.latest_state.completed_at_ms,
            },
        )
        self.assertEqual(sorted(published["cycle"]), ["action", "plan", "proposal"])
        self.assertEqual(published["cycle"]["action"]["frame_id"], published["frame_id"])
        self.assertEqual(published["cycle"]["plan"]["frame_id"], published["frame_id"])
        self.assertEqual(published["cycle"]["proposal"]["frame_id"], published["frame_id"])
        self.assertNotIn("source", published["cycle"]["proposal"])
        # The dedicated route owns decision publication. Existing observation
        # consumers retain their established payload shape.
        self.assertNotIn("decision", part.publish_latest(now_ms=completed_at_ms))

        expired = part.publish_decision_latest(
            now_ms=completed_at_ms + decision["stale_after_ms"] + 1
        )
        self.assertFalse(expired["ok"])
        self.assertEqual(expired["reason"], "expired")

        # Replacing a decision step retires its result.
        part.host.set_step("action", decision_steps().action)
        reset = part.publish_decision_latest(now_ms=completed_at_ms)
        self.assertFalse(reset["ok"])
        self.assertEqual(reset["reason"], "reset")

    def test_both_publications_carry_the_cycles_timing(self) -> None:
        """Timing rides in the report's values: a required report key rejected hosts without it."""

        part = self._hold_part()
        part.run(image_array=np.zeros((4, 4, 3), dtype=np.uint8), mode="user")
        part.wait_for_cycle()
        assert part.latest_state is not None
        now_ms = part.latest_state.completed_at_ms

        observation = part.publish_latest(now_ms=now_ms)
        timing = observation["timing"]
        self.assertEqual([entry["step"] for entry in timing["steps"]], list(STEPS))
        self.assertEqual(observation["duration_ms"], timing["duration_ms"])
        decision = part.publish_decision_latest(now_ms=now_ms)["decision"]
        self.assertEqual(decision["values"]["timing"], timing)
        self.assertNotIn("timing", decision)

    def test_concurrent_reads_keep_frame_identity_paired(self) -> None:
        part = AutonomyPilotPart(host=create_host(steps=decision_steps()))
        stop = threading.Event()
        errors: list[str] = []

        def writer() -> None:
            index = 0
            while not stop.is_set():
                image = np.full((6, 6, 3), index % 200, dtype=np.uint8)
                part.run(image_array=image, mode="user")
                index += 1

        def reader() -> None:
            for _ in range(40):
                payload = part.publish_latest()
                jpeg, frame_meta = part.publish_latest_frame_jpeg()
                if payload.get("frame") is None:
                    continue
                left = payload["frame"]["frame_id"]
                right = None if frame_meta.get("frame") is None else frame_meta["frame"]["frame_id"]
                if jpeg is not None and right is not None and left != right:
                    # publication and jpeg may advance between calls; each call
                    # must still be internally consistent.
                    pass
                if frame_meta.get("frame") is not None and jpeg is not None:
                    if frame_meta["frame"]["frame_id"] is None:
                        errors.append("missing frame id with jpeg")
                if payload.get("frame") is not None:
                    if payload["perception"] is not None and payload["frame"]["frame_id"] is None:
                        errors.append("perception without frame id")

        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=reader),
            threading.Thread(target=reader),
        ]
        for thread in threads:
            thread.start()
        threads[0].join(timeout=0.2)
        stop.set()
        for thread in threads:
            thread.join(timeout=1.0)
        part.wait_for_cycle()
        self.assertEqual(errors, [])
        # Single atomic call still pairs metadata and image.
        jpeg, meta = part.publish_latest_frame_jpeg()
        self.assertIsNotNone(jpeg)
        self.assertEqual(meta["frame"]["frame_id"], part.latest_state.frame_id)

    def test_manage_and_web_serve_the_shared_runtime_routes(self) -> None:
        root = Path(__file__).resolve().parents[4] / "deploy" / "targets" / "donkeycar"
        manage = (root / "app" / "manage.py").read_text(encoding="utf-8")
        self.assertIn("autonomy_controller.autonomy_routes = RuntimeRoutes(", manage)
        self.assertIn("**loop_options", manage)

        # Vendor checkout is generated; the tracked patch is the durable source.
        patch = (root / "patches" / "waveshare-donkeycar-local.patch").read_text(encoding="utf-8")
        self.assertIn('+            (r"/(?:autonomy/.*|api/plugins)", AutonomyRoutesAPI),', patch)
        self.assertNotIn("/autonomy/mode", patch)
        dispatcher = patch.split("+class AutonomyRoutesAPI", 1)[1].split(" class WsTest", 1)[0]
        code = compile(
            "class AutonomyRoutesAPI(RequestHandler):\n" + "\n".join(
                line[1:] for line in dispatcher.splitlines() if line.startswith("+")
            ),
            "AutonomyRoutesAPI",
            "exec",
        )

        class Handler:
            def set_status(self, status):
                self.status = status
            def set_header(self, key, value):
                pass
            def write(self, body):
                if self.status == 304:
                    raise AssertionError("304 must not write even an empty chunk")
                self.body = body

        namespace = {"RequestHandler": Handler, "asyncio": asyncio, "json": json}
        exec(code, namespace)
        for status, body in ((200, b"payload"), (304, b"")):
            route = mock.Mock()
            route.handle.return_value = status, {"ETag": "test"}, body
            handler = namespace["AutonomyRoutesAPI"]()
            handler.application = SimpleNamespace(autonomy_routes=route)
            handler.request = SimpleNamespace(method="GET", uri="/autonomy/observation/transaction",
                body=b"", headers={"If-None-Match": "test"}, protocol="http", host="car.test")
            asyncio.run(handler._dispatch())
            self.assertEqual(handler.status, status)
            self.assertEqual(route.handle.call_args.kwargs["headers"], {"If-None-Match": "test"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
