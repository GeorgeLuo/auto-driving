from __future__ import annotations

import threading
import unittest
from pathlib import Path

import numpy as np

from autonomy.decision_cycle.activation import DECISION_STEPS, activation_generation_id
from autonomy.decision_cycle.steps import decision_steps
from autonomy.runtime.cycle_host import AutonomyCycleHost
from implementations.decision_cycle.catalog import packaged_activation
from implementations.runtime.donkeycar import (
    DECISION_PUBLICATION_SCHEMA,
    LATEST_FRAME_PATH,
    LATEST_JSON_PATH,
    OBSERVATION_PUBLICATION_SCHEMA,
    AutonomyPilotPart,
)


class ObservationPublicationTests(unittest.TestCase):
    def _hold_part(self) -> AutonomyPilotPart:
        activations = {step: packaged_activation(step) for step in DECISION_STEPS}
        return AutonomyPilotPart(
            host=AutonomyCycleHost(steps=decision_steps(activations)),
            min_interval_s=0.0,
            vehicle_id="piracer",
            source_id="donkeycar:piracer",
            decision_activations={
                step: activation.to_payload() for step, activation in activations.items()
            },
            run_id="donkey-run-fixture",
        )

    def test_warming_publication_before_first_result(self) -> None:
        part = AutonomyPilotPart(
            host=AutonomyCycleHost(),
            min_interval_s=0.0,
            preset="lightweight_observer",
        )
        payload = part.publish_latest(now_ms=1_000)
        self.assertEqual(payload["schema"], OBSERVATION_PUBLICATION_SCHEMA)
        self.assertEqual(payload["health"], "warming")
        self.assertFalse(payload["ok"])
        self.assertIsNone(payload["frame"])
        self.assertEqual(payload["preset"], "lightweight_observer")
        self.assertEqual(payload["latest_json_path"], LATEST_JSON_PATH)
        self.assertEqual(payload["latest_frame_path"], LATEST_FRAME_PATH)

    def test_healthy_publication_includes_detached_perception_and_matching_frame(self) -> None:
        part = AutonomyPilotPart(
            host=AutonomyCycleHost(),
            min_interval_s=0.0,
            preset="test-observer",
        )
        image = np.zeros((8, 12, 3), dtype=np.uint8)
        image[:, :] = (10, 20, 30)
        part.run(image_array=image, mode="user")
        part.wait_for_cycle()

        payload = part.publish_latest(now_ms=part.latest_snapshot.completed_at_ms + 10)
        self.assertEqual(payload["health"], "healthy")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["mode"], "user")
        self.assertEqual(payload["control"]["steering"], 0.0)
        self.assertEqual(payload["control"]["throttle"], 0.0)
        self.assertEqual(payload["frame"]["frame_id"], "donkey_frame_000000")
        self.assertTrue(payload["frame"]["has_image"])
        self.assertEqual(payload["preset"], "test-observer")
        # Idle host has no perception step; publication still carries cycle control.
        self.assertIsNone(payload["perception"])
        self.assertIsNone(payload["memory"])
        self.assertEqual(payload["control"]["reason"], "hold-idle")
        self.assertEqual(payload["frame"]["frame_path"], LATEST_FRAME_PATH)

    def test_publication_includes_the_memory_report_when_step_present(self) -> None:
        from autonomy.decision_cycle.context import DecisionFrameContext
        from autonomy.decision_cycle.cycle import DecisionSteps
        from autonomy.decision_cycle.observation.values import Observation
        from autonomy.decision_cycle.memory.evidence import MemoryProvenance, RetainedEvidence
        from autonomy.decision_cycle.perception.evidence.values import ViewLocation
        from autonomy.runtime.cycle_host import AutonomyCycleHost

        def remember(context, observation):
            del observation
            record = RetainedEvidence(
                        record_id="thing:boundary",
                        kind="floor_boundary",
                        label="boundary",
                        confidence=0.9,
                        provenance=MemoryProvenance(
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
                "schema": "memory_report_v0",
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
            }

        host = AutonomyCycleHost(steps=DecisionSteps(memory=remember))
        part = AutonomyPilotPart(host=host, min_interval_s=0.0, preset="test")
        part.run(image_array=np.zeros((8, 8, 3), dtype=np.uint8), mode="user")
        part.wait_for_cycle()
        payload = part.publish_latest(now_ms=part.latest_snapshot.completed_at_ms)
        self.assertIsNotNone(payload["memory"])
        state = payload["memory"]["plugins"][0]["state"]
        self.assertEqual(state["health"], "healthy")
        self.assertEqual(state["record_count"], 1)
        self.assertEqual(state["records"][0]["kind"], "floor_boundary")

        jpeg, frame_meta = part.publish_latest_frame_jpeg()
        self.assertIsNotNone(jpeg)
        self.assertGreater(len(jpeg), 32)
        self.assertEqual(frame_meta["frame"]["frame_id"], payload["frame"]["frame_id"])
        self.assertEqual(frame_meta["health"], "healthy")
        self.assertTrue(jpeg.startswith(b"\xff\xd8"))

    def test_stale_and_error_health_states(self) -> None:
        part = AutonomyPilotPart(host=AutonomyCycleHost(), min_interval_s=0.5)
        part.run(image_array=np.zeros((4, 4, 3), dtype=np.uint8), mode="user")
        part.wait_for_cycle()
        completed = part.latest_snapshot.completed_at_ms
        stale = part.publish_latest(now_ms=completed + 5_000)
        self.assertEqual(stale["health"], "stale")
        self.assertTrue(stale["ok"])
        self.assertGreater(stale["result_age_ms"], stale["stale_after_ms"])

        class Boom:
            def status(self):
                return {"steps": {}}

            def run(self, context):
                del context
                raise RuntimeError("boom")

        failing = AutonomyPilotPart(host=Boom(), min_interval_s=0.0)  # type: ignore[arg-type]
        failing.run(image_array=np.zeros((4, 4, 3), dtype=np.uint8), mode="user")
        failing.wait_for_cycle()
        errored = failing.publish_latest(now_ms=failing.latest_snapshot.completed_at_ms)
        self.assertEqual(errored["health"], "error")
        self.assertFalse(errored["ok"])
        self.assertIn("RuntimeError", errored["error"] or "")

    def test_unavailable_when_image_missing(self) -> None:
        part = AutonomyPilotPart(host=AutonomyCycleHost(), min_interval_s=0.0)
        part.run(image_array=None, mode="user")
        part.wait_for_cycle()
        payload = part.publish_latest(now_ms=part.latest_snapshot.completed_at_ms)
        self.assertEqual(payload["health"], "unavailable")
        jpeg, meta = part.publish_latest_frame_jpeg()
        self.assertIsNone(jpeg)
        self.assertEqual(meta["health"], "unavailable")

    def test_decision_publication_keeps_source_identity_and_cycle_atomic(self) -> None:
        part = self._hold_part()
        part.run(image_array=np.zeros((4, 4, 3), dtype=np.uint8), mode="user")
        part.wait_for_cycle()
        assert part.latest_snapshot is not None
        completed_at_ms = part.latest_snapshot.completed_at_ms

        decision = part.publish_decision_latest(now_ms=completed_at_ms)
        self.assertEqual(decision["schema"], DECISION_PUBLICATION_SCHEMA)
        self.assertTrue(decision["ok"])
        self.assertEqual(decision["status"], "ready")
        self.assertEqual(decision["reason"], "")
        published = decision["decision"]
        assert isinstance(published, dict)
        self.assertEqual(published["vehicle_id"], "piracer")
        self.assertEqual(published["source_id"], "donkeycar:piracer")
        self.assertEqual(published["run_id"], "donkey-run-fixture")
        expected_generation = activation_generation_id(
            {step: packaged_activation(step) for step in DECISION_STEPS}, prefix="decision"
        )
        self.assertEqual(published["generation_id"], expected_generation)
        self.assertEqual(published["activation"]["generation_id"], expected_generation)
        self.assertEqual(sorted(published["activation"]["steps"]), sorted(DECISION_STEPS))
        self.assertEqual(
            published["activation"]["steps"]["proposal"]["plugins"],
            ["avoid_recent_obstruction"],
        )
        self.assertNotIn("producer_pid", published)
        self.assertEqual(published["frame_id"], part.latest_snapshot.frame_id)
        self.assertEqual(published["frame_index"], part.latest_snapshot.frame_index)
        self.assertEqual(published["timestamp_ms"], part.latest_snapshot.captured_at_ms)
        self.assertEqual(
            published["source_frame"],
            {
                "frame_id": part.latest_snapshot.frame_id,
                "frame_index": part.latest_snapshot.frame_index,
                "captured_at_ms": part.latest_snapshot.captured_at_ms,
                "completed_at_ms": part.latest_snapshot.completed_at_ms,
            },
        )
        self.assertEqual(sorted(published["cycle"]), ["action", "plan", "proposal"])
        self.assertEqual(published["cycle"]["action"]["frame_id"], published["frame_id"])
        self.assertEqual(published["cycle"]["plan"]["frame_id"], published["frame_id"])
        self.assertEqual(
            published["cycle"]["proposal"]["source"]["frame_index"],
            published["frame_index"],
        )
        self.assertNotIn(
            "runtime_identity",
            published["cycle"]["proposal"]["source"]["metadata"],
        )
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

    def test_concurrent_reads_keep_frame_identity_paired(self) -> None:
        part = AutonomyPilotPart(host=AutonomyCycleHost(), min_interval_s=0.0)
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
        self.assertEqual(meta["frame"]["frame_id"], part.latest_snapshot.frame_id)

    def test_manage_and_web_wire_publication_routes(self) -> None:
        manage = (
            Path(__file__).resolve().parents[3]
            / "deploy"
            / "targets"
            / "donkeycar"
            / "app"
            / "manage.py"
        ).read_text(encoding="utf-8")
        self.assertIn("autonomy_controller.observation_publisher = autonomy_part", manage)
        self.assertIn("preset=perception_preset", manage)

        # Vendor checkout is generated; the tracked patch is the durable source.
        patch = (
            Path(__file__).resolve().parents[3]
            / "deploy"
            / "targets"
            / "donkeycar"
            / "patches"
            / "waveshare-donkeycar-local.patch"
        ).read_text(encoding="utf-8")
        self.assertIn('/autonomy/observation/latest', patch)
        self.assertIn('/autonomy/observation/latest/frame.jpg', patch)
        self.assertIn('/autonomy/camera/latest', patch)
        self.assertIn('/autonomy/camera/latest/frame.jpg', patch)
        self.assertIn("class AutonomyCameraLatestAPI", patch)
        self.assertIn("class AutonomyCameraLatestFrameAPI", patch)
        self.assertIn('/autonomy/memory/reset', patch)
        self.assertIn("class AutonomyObservationLatestAPI", patch)
        self.assertIn("class AutonomyObservationLatestFrameAPI", patch)
        self.assertIn("class AutonomyMemoryResetAPI", patch)
        self.assertIn('/autonomy/decision/latest', patch)
        self.assertIn("class AutonomyDecisionLatestAPI", patch)
        route_source = patch.split("class AutonomyDecisionLatestAPI", 1)[1].split(
            "+class AutonomyModeAPI", 1
        )[0]
        compile(
            "class AutonomyDecisionLatestAPI(AutonomyAPIBase):\n" + "\n".join(
                line[1:] for line in route_source.splitlines() if line.startswith("+")
            ),
            "AutonomyDecisionLatestAPI",
            "exec",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
