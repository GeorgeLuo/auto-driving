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
from autonomy.runtime.cycle_host import AutonomyCycleHost
from implementations.decision_cycle.catalog import packaged_activation, selection_activation
from autonomy.runtime.frame_loop import (
    FRAME_PUBLICATION_SCHEMA,
    OBSERVATION_TRANSACTION_PATH,
)
from implementations.runtime.picar import AutonomyPilotPart, DonkeyControlTarget, create_host
from implementations.runtime.picar.host_telemetry import DriveModeTelemetryAdapter, HostTelemetryStore


class ObservationPublicationTests(unittest.TestCase):
    def _transaction_image(self, part):
        status, _, body = RuntimeRoutes(part).handle("GET", OBSERVATION_TRANSACTION_PATH)
        self.assertEqual(status, 200, body)
        payload = json.loads(body)
        self.assertEqual(payload["image_frame_id"], payload["publication"]["report"]["frame_id"])
        return base64.b64decode(payload["image_base64"], validate=True)

    def test_repeated_image_reads_encode_each_processed_frame_once(self) -> None:
        from autonomy.runtime.frame_loop import encode_jpeg

        part = self._hold_part()
        self.addCleanup(part.shutdown)
        part.run(image_array=np.zeros((8, 12, 3), dtype=np.uint8), mode="user")
        part.wait_for_cycle()
        with mock.patch("autonomy.runtime.frame_loop.encode_jpeg", wraps=encode_jpeg) as encode:
            first = self._transaction_image(part)
            first_id = part.publish_latest()["report"]["frame_id"]
            self.assertEqual(self._transaction_image(part), first)
            self.assertEqual(encode.call_count, 1)
            part.run(image_array=np.full((8, 12, 3), 200, dtype=np.uint8), mode="user")
            part.wait_for_cycle()
            second = self._transaction_image(part)
            self.assertNotEqual(first, second)
            self.assertNotEqual(first_id, part.publish_latest()["report"]["frame_id"])
            self.assertEqual(encode.call_count, 2)

    def test_wire_jpeg_is_reused_by_decision_and_recording(self):
        from autonomy.runtime.frame_loop import encode_jpeg
        image = np.full((8, 12, 3), 70, dtype=np.uint8)
        wire = encode_jpeg(image)
        part = self._hold_part()
        self.addCleanup(part.shutdown)
        with tempfile.TemporaryDirectory() as tmp:
            part.recording_root = Path(tmp)
            part.start(RunConfiguration(mode="observe_only", num_decisions=None), record=True)
            with mock.patch("autonomy.runtime.frame_loop.encode_jpeg", side_effect=AssertionError("reencoded")):
                part.submit(image, jpeg_bytes=wire)
                part.wait_for_cycle()
                decision = self._transaction_image(part)
            self.assertEqual(decision, wire)
            files = list(Path(tmp).glob("*/frames/*.jpg"))
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].read_bytes(), wire)
            self.assertNotIn("jpeg_bytes", part.host.last_context.sensor_frame.readings["front_camera"].to_dict())

    def test_recording_and_transaction_share_one_encode(self):
        from autonomy.runtime.frame_loop import encode_jpeg
        part = self._hold_part()
        self.addCleanup(part.shutdown)
        with tempfile.TemporaryDirectory() as tmp:
            part.recording_root = Path(tmp)
            part.start(RunConfiguration(mode="observe_only", num_decisions=None), record=True)
            with mock.patch("autonomy.runtime.frame_loop.encode_jpeg", wraps=encode_jpeg) as encoder:
                part.run(image_array=np.zeros((8, 12, 3), dtype=np.uint8), mode="user")
                part.wait_for_cycle()
                decision = self._transaction_image(part)
                self.assertEqual(encoder.call_count, 1)
            files = list(Path(tmp).glob("*/frames/*.jpg"))
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].read_bytes(), decision)

    def _hold_part(self, **options) -> AutonomyPilotPart:
        activations = {step: packaged_activation(step) for step in DECISION_STEPS}
        return AutonomyPilotPart(
            host=create_host(steps=decision_steps(activations)),
            host_name="piracer",
            source_id="donkeycar:piracer",
            run_id="donkey-run-fixture",
            **options,
        )

    def test_warming_publication_before_first_result(self) -> None:
        part = AutonomyPilotPart(host=create_host(steps=decision_steps()))
        payload = part.publish_latest(now_ms=1_000)
        self.assertEqual(payload["schema"], FRAME_PUBLICATION_SCHEMA)
        self.assertEqual(payload["health"], "warming")
        self.assertFalse(payload["ok"])
        self.assertIsNone(payload["report"])
        self.assertIsNone(part.observation_status()["latest"])

    def test_healthy_publication_includes_detached_perception_and_matching_frame(self) -> None:
        part = AutonomyPilotPart(host=create_host(steps=decision_steps()))
        image = np.zeros((8, 12, 3), dtype=np.uint8)
        image[:, :] = (10, 20, 30)
        part.run(image_array=image, mode="user")
        part.wait_for_cycle()

        payload = part.publish_latest(now_ms=part.latest_state.completed_at_ms + 10)
        report = payload["report"]
        self.assertEqual(payload["health"], "healthy")
        self.assertTrue(payload["ok"])
        self.assertEqual(report["status"], "ok")
        self.assertIsNone(report["error"])
        self.assertEqual(report["application"]["mode"], "manual")
        self.assertEqual(report["application"]["steering"], 0.0)
        self.assertEqual(report["application"]["throttle"], 0.0)
        self.assertEqual(report["frame_id"], "donkey_frame_000000")
        self.assertEqual(
            sorted(report["cycle"]),
            ["action", "memory", "observation", "perception", "plan", "proposal"],
        )
        # Idle host has no perception step, so that record stays null.
        self.assertIsNone(report["cycle"]["perception"])
        self.assertIsNone(report["cycle"]["memory"])
        self.assertEqual(report["application"]["reason"], "manual")
        self.assertFalse(report["application"]["applied"])
        self.assertEqual(
            report["cycle"]["action"]["authority"]["authorized_output"]["reason"],
            "action_plan_invariant_violated",
        )
        self.assertTrue(self._transaction_image(part).startswith(b"\xff\xd8"))

    def test_presets_follow_the_activations_each_frame_applied(self) -> None:
        """Regression: the runtime reported only perception's preset, fixed at start,
        so memory's and proposal's presets, and any restaged preset, never showed."""

        activations = {step: selection_activation(step) for step in DECISION_STEPS}
        part = AutonomyPilotPart(host=create_host(steps=decision_steps(activations)))
        self.assertIsNone(part.publish_latest(now_ms=1_000)["report"])

        part.run(image_array=np.zeros((8, 8, 3), dtype=np.uint8), mode="user")
        part.wait_for_cycle()
        payload = part.publish_latest(now_ms=part.latest_state.completed_at_ms)
        applied = {
            step: activation.metadata["preset"]
            for step, activation in activations.items()
            if activation.metadata.get("preset")
        }
        steps = payload["report"]["values"]["activation"]["steps"]
        shown = {
            step: steps[step]["metadata"]["preset"]
            for step in steps
            if isinstance(steps[step], dict) and steps[step].get("metadata", {}).get("preset")
        }
        self.assertEqual(shown, applied)

    def test_publication_includes_the_memory_report_when_step_present(self) -> None:
        from autonomy.decision_cycle.cycle import DecisionSteps
        from autonomy.decision_cycle.memory.evidence import MemoryOrigin, RetainedEvidence
        from autonomy.decision_cycle.perception.evidence.values import ViewLocation

        def remember(context):
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
                "schema": "memory_report",
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
        report = payload["report"]["cycle"]["memory"]
        self.assertIsNotNone(report)
        state = report["plugins"][0]["state"]
        self.assertEqual(state["health"], "healthy")
        self.assertEqual(state["record_count"], 1)
        self.assertEqual(state["records"][0]["kind"], "floor_boundary")
        self.assertEqual(report["evidence_publisher"], "bounded_evidence")

        self.assertTrue(self._transaction_image(part).startswith(b"\xff\xd8"))

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

        report = part.publish_latest(now_ms=part.latest_state.completed_at_ms)["report"]["cycle"]["memory"]

        states = {entry["plugin_id"]: entry["state"] for entry in report["plugins"]}
        self.assertEqual(list(states), ["recording_test", "bounded_evidence"])
        self.assertEqual(states["recording_test"]["record_count"], 1)
        self.assertEqual(states["bounded_evidence"]["record_count"], 1)
        self.assertEqual(report["evidence_publisher"], "bounded_evidence")
        self.assertEqual(
            part.host.status()["steps"]["memory"]["evidence_publisher"], "bounded_evidence",
        )

    def test_stale_and_error_health_states(self) -> None:
        part = AutonomyPilotPart(host=create_host(steps=decision_steps()))
        part.run(image_array=np.zeros((4, 4, 3), dtype=np.uint8), mode="user")
        part.wait_for_cycle()
        completed = part.latest_state.completed_at_ms
        stale = part.publish_latest(now_ms=completed + 5_000)
        self.assertEqual(stale["health"], "stale")
        self.assertTrue(stale["ok"])
        self.assertGreater(stale["result_age_ms"], stale["stale_after_ms"])

        class Boom(AutonomyCycleHost):
            """A host whose cycle raises before it produces any result."""

            def run(self, context, **inputs):
                del context
                raise RuntimeError("boom")

        failing = AutonomyPilotPart(host=Boom(target=DonkeyControlTarget()))
        failing.run(image_array=np.zeros((4, 4, 3), dtype=np.uint8), mode="user")
        failing.wait_for_cycle()
        errored = failing.publish_latest(now_ms=failing.latest_state.completed_at_ms)
        self.assertEqual(errored["health"], "healthy")
        self.assertTrue(errored["ok"])
        report = errored["report"]
        self.assertEqual(report["status"], "error")
        self.assertIn("RuntimeError", report["error"])
        self.assertEqual(report["application"]["reason"], "observation-cycle-error")
        self.assertFalse(report["application"]["applied"])
        for step in ("perception", "observation", "memory", "proposal", "plan", "action"):
            self.assertIsNone(report["cycle"][step])

    def test_unavailable_when_image_missing(self) -> None:
        part = AutonomyPilotPart(host=create_host(steps=decision_steps()))
        part.run(image_array=None, mode="user")
        part.wait_for_cycle()
        payload = part.publish_latest(now_ms=part.latest_state.completed_at_ms)
        self.assertEqual(payload["health"], "healthy")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["report"]["status"], "ok")
        status, _, body = RuntimeRoutes(part).handle("GET", OBSERVATION_TRANSACTION_PATH)
        transaction = json.loads(body)
        self.assertEqual(status, 200)
        self.assertIsNone(transaction["image_base64"])
        self.assertEqual(transaction["image_error"], "image_unavailable")
        self.assertEqual(transaction["publication"]["health"], "healthy")
        self.assertEqual(transaction["publication"]["report"]["frame_id"], payload["report"]["frame_id"])

    def test_decision_publication_keeps_source_identity_and_cycle_atomic(self) -> None:
        store = HostTelemetryStore(
            host_name="piracer", source_id="donkeycar:piracer", run_id="donkey-run-fixture",
            generation_id="decision-startup",
        )
        part = self._hold_part(host_telemetry=DriveModeTelemetryAdapter(store))
        part.run(image_array=np.zeros((4, 4, 3), dtype=np.uint8), mode="user")
        part.wait_for_cycle()
        assert part.latest_state is not None
        completed_at_ms = part.latest_state.completed_at_ms

        decision = part.publish_latest(now_ms=completed_at_ms)
        self.assertEqual(decision["schema"], FRAME_PUBLICATION_SCHEMA)
        self.assertTrue(decision["ok"])
        self.assertEqual(decision["health"], "healthy")
        published = decision["report"]
        assert isinstance(published, dict)
        self.assertEqual(published["status"], "ok")
        self.assertIsNone(published["error"])
        self.assertEqual(published["schema"], "host_report")
        self.assertEqual(published["host_name"], "piracer")
        self.assertNotIn("vehicle_id", published)
        values = published["values"]
        self.assertEqual(values["source_id"], "donkeycar:piracer")
        self.assertEqual(published["run_id"], "donkey-run-fixture")
        expected_generation = activation_generation_id(
            {step: values["activation"]["steps"][step] for step in STEPS}, prefix="decision"
        )
        self.assertEqual(published["generation_id"], expected_generation)
        self.assertEqual(values["activation"]["generation_id"], expected_generation)
        self.assertEqual(expected_generation, part.host.applied_decision()["generation_id"])
        # Telemetry names the generation of the frame it reports.
        self.assertEqual(store.generation_id, expected_generation)
        self.assertEqual(sorted(values["activation"]["steps"]), sorted(STEPS))
        self.assertEqual(
            values["activation"]["steps"]["proposal"]["plugins"],
            ["avoid_recent_obstruction"],
        )
        self.assertNotIn("producer_pid", published)
        self.assertEqual(published["frame_id"], part.latest_state.frame_id)
        self.assertEqual(published["frame_index"], part.latest_state.frame_index)
        self.assertEqual(published["timestamp_ms"], part.latest_state.captured_at_ms)
        # The report's own fields are its frame identity; values do not repeat them.
        self.assertNotIn("source_frame", values)
        self.assertEqual(
            sorted(published["cycle"]),
            ["action", "memory", "observation", "perception", "plan", "proposal"],
        )
        self.assertEqual(published["cycle"]["action"]["frame_id"], published["frame_id"])
        self.assertEqual(published["cycle"]["plan"]["frame_id"], published["frame_id"])
        self.assertEqual(published["cycle"]["proposal"]["frame_id"], published["frame_id"])
        self.assertNotIn("source", published["cycle"]["proposal"])
        self.assertIsNone(published["cycle"]["perception"])

        expired = part.publish_latest(
            now_ms=completed_at_ms + decision["stale_after_ms"] + 1
        )
        self.assertTrue(expired["ok"])
        self.assertEqual(expired["health"], "stale")
        self.assertEqual(expired["report"]["frame_id"], published["frame_id"])
        self.assertEqual(expired["report"]["status"], "ok")

        # A request leaves the completed frame alone until the next cycle adopts it.
        replacement = "hold" if part.host.step("action").plugin_ids != ("hold",) else "selected"
        self.assertTrue(part.host.arm({"action": [replacement]})["ok"])
        kept = part.publish_latest(now_ms=completed_at_ms)
        self.assertEqual(kept["report"]["frame_id"], published["frame_id"])
        self.assertEqual(kept["report"]["cycle"]["action"]["frame_id"], published["frame_id"])
        part.run(image_array=np.zeros((4, 4, 3), dtype=np.uint8), mode="user")
        part.wait_for_cycle()
        restaged = part.latest_state.report["generation_id"]
        self.assertNotEqual(restaged, expected_generation)
        self.assertEqual(restaged, part.host.applied_decision()["generation_id"])
        self.assertEqual(part.latest_state.report["values"]["activation"]["generation_id"], restaged)
        self.assertEqual(store.generation_id, restaged)
        self.assertNotEqual(part.latest_state.frame_id, published["frame_id"])

    def test_both_publications_carry_the_cycles_timing(self) -> None:
        """Timing rides in the report's values: a required report key rejected hosts without it."""

        part = self._hold_part()
        part.run(image_array=np.zeros((4, 4, 3), dtype=np.uint8), mode="user")
        part.wait_for_cycle()
        assert part.latest_state is not None
        now_ms = part.latest_state.completed_at_ms

        observation = part.publish_latest(now_ms=now_ms)
        timing = observation["report"]["values"]["timing"]
        self.assertEqual([entry["step"] for entry in timing["steps"]], list(STEPS))
        self.assertNotIn("timing", observation)
        self.assertNotIn("duration_ms", observation)

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
                report = payload.get("report")
                if report is None:
                    continue
                perception = report.get("cycle", {}).get("perception")
                if perception is not None and not report.get("frame_id"):
                    errors.append("perception without frame id")
                status, _, body = RuntimeRoutes(part).handle("GET", OBSERVATION_TRANSACTION_PATH)
                if status != 200:
                    continue
                transaction = json.loads(body)
                published = transaction["publication"].get("report")
                if published is None or transaction["image_base64"] is None:
                    continue
                if transaction["image_frame_id"] != published["frame_id"]:
                    errors.append("transaction image advanced away from its report")

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
        status, _, body = RuntimeRoutes(part).handle("GET", OBSERVATION_TRANSACTION_PATH)
        transaction = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(transaction["image_frame_id"], transaction["publication"]["report"]["frame_id"])
        self.assertEqual(transaction["image_frame_id"], part.latest_state.frame_id)

    def test_recording_keeps_one_error_frame_and_rebuilds_the_report(self) -> None:
        from autonomy.decision_cycle.cycle import DecisionSteps
        from autonomy.decision_cycle.observation.values import Observation
        from autonomy.decision_cycle.perception.interface import (
            PERCEPTION_TEXT_SCHEMA,
            PerceptionText,
        )

        calls: list[str] = []

        def perceive(_context):
            text = PerceptionText(
                schema=PERCEPTION_TEXT_SCHEMA,
                plugin_id=f"seen-{len(calls)}",
                status="ok",
                signals=(),
                things=(),
                limits=(),
            )
            calls.append(text.plugin_id)
            return text

        def observe(context, _perception):
            return Observation(context.frame_id, context.timestamp_ms, {})

        def propose(_context):
            if len(calls) > 1:
                raise RuntimeError("proposal failed")
            return None

        part = AutonomyPilotPart(
            host=create_host(steps=DecisionSteps(
                perception=perceive, observation=observe, proposal=propose,
            )),
            host_name="piracer",
            source_id="donkeycar:piracer",
            run_id="error-frame-run",
        )
        self.addCleanup(part.shutdown)
        with tempfile.TemporaryDirectory() as tmp:
            part.recording_root = Path(tmp)
            part.start(RunConfiguration(mode="observe_only", num_decisions=None), record=True)
            part.submit(np.zeros((4, 4, 3), dtype=np.uint8), frame_id="first")
            part.wait_for_cycle()
            first = part.publish_latest()["report"]
            self.assertEqual(first["status"], "ok")
            self.assertEqual(first["cycle"]["perception"]["plugin_id"], "seen-0")
            part.submit(np.zeros((4, 4, 3), dtype=np.uint8), frame_id="second")
            part.wait_for_cycle()
            published = part.publish_latest()["report"]
            self.assertEqual(published["frame_id"], "second")
            self.assertEqual(published["status"], "error")
            self.assertIn("proposal failed", published["error"])
            self.assertEqual(published["cycle"]["perception"]["plugin_id"], "seen-1")
            self.assertEqual(published["cycle"]["observation"]["observation_id"], "second")
            for step in ("memory", "proposal", "plan", "action"):
                self.assertIsNone(published["cycle"][step])
            timing = {
                entry["step"]: entry["duration_ms"]
                for entry in published["values"]["timing"]["steps"]
            }
            self.assertIsInstance(timing["perception"], float)
            self.assertIsInstance(timing["observation"], float)
            self.assertIsNone(timing["proposal"])
            frames = part.host.recording.read(run_id=part.host.recording.run_id, after=0)["frames"]
            self.assertEqual([item["frame"]["frame_id"] for item in frames], ["first", "second"])
            recorded = frames[1]["frame"]
            self.assertNotIn("lines", recorded["perception"])
            self.assertNotIn("text", recorded["perception"])
            self.assertFalse(list(part.host.recording.root.rglob("*.txt")))
            self.assertEqual(recorded["status"], published["status"])
            self.assertEqual(recorded["error"], published["error"])
            self.assertEqual(recorded["application"], published["application"])
            self.assertEqual(recorded["host_name"], published["host_name"])
            self.assertEqual(recorded["run_id"], part.host.recording.run_id)
            self.assertEqual(recorded["generation_id"], published["generation_id"])
            for step in ("perception", "observation", "memory", "proposal", "plan", "action"):
                self.assertEqual(recorded[step], published["cycle"][step])
            self.assertNotEqual(
                recorded["perception"]["plugin_id"], frames[0]["frame"]["perception"]["plugin_id"],
            )

    def test_manage_and_web_serve_the_shared_runtime_routes(self) -> None:
        root = Path(__file__).resolve().parents[4] / "deploy" / "targets" / "donkeycar"
        manage = (root / "app" / "manage.py").read_text(encoding="utf-8")
        self.assertIn("autonomy_controller.autonomy_routes = RuntimeRoutes(", manage)
        self.assertIn("**loop_options", manage)

        # Vendor checkout is generated; the tracked patch is the durable source.
        patch = (root / "patches" / "waveshare-donkeycar-local.patch").read_text(encoding="utf-8")
        self.assertIn('+            (r"/(?:autonomy/.*|api/plugins)", AutonomyRoutesAPI),', patch)
        self.assertNotIn("/host/mode", patch)
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
            handler.request = SimpleNamespace(method="GET", uri="/host/observation/transaction",
                body=b"", headers={"If-None-Match": "test"}, protocol="http", host="car.test")
            asyncio.run(handler._dispatch())
            self.assertEqual(handler.status, status)
            self.assertEqual(route.handle.call_args.kwargs["headers"], {"If-None-Match": "test"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
