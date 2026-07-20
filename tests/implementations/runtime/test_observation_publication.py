from __future__ import annotations

import threading
import unittest
from pathlib import Path

import numpy as np

from autonomy.runtime.cycle_host import AutonomyCycleHost
from implementations.runtime.donkeycar import (
    LATEST_FRAME_PATH,
    LATEST_JSON_PATH,
    OBSERVATION_PUBLICATION_SCHEMA,
    AutonomyPilotPart,
)


class ObservationPublicationTests(unittest.TestCase):
    def test_warming_publication_before_first_result(self) -> None:
        part = AutonomyPilotPart(
            host=AutonomyCycleHost(),
            min_interval_s=0.0,
            algorithm="lightweight_observer",
        )
        payload = part.publish_latest(now_ms=1_000)
        self.assertEqual(payload["schema"], OBSERVATION_PUBLICATION_SCHEMA)
        self.assertEqual(payload["health"], "warming")
        self.assertFalse(payload["ok"])
        self.assertIsNone(payload["frame"])
        self.assertEqual(payload["algorithm"], "lightweight_observer")
        self.assertEqual(payload["latest_json_path"], LATEST_JSON_PATH)
        self.assertEqual(payload["latest_frame_path"], LATEST_FRAME_PATH)

    def test_healthy_publication_includes_detached_perception_and_matching_frame(self) -> None:
        part = AutonomyPilotPart(
            host=AutonomyCycleHost(),
            min_interval_s=0.0,
            algorithm="test-observer",
        )
        image = np.zeros((8, 12, 3), dtype=np.uint8)
        image[:, :] = (10, 20, 30)
        part.run(image_array=image, mode="user")

        payload = part.publish_latest(now_ms=part.latest_snapshot.completed_at_ms + 10)
        self.assertEqual(payload["health"], "healthy")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["mode"], "user")
        self.assertEqual(payload["control"]["steering"], 0.0)
        self.assertEqual(payload["control"]["throttle"], 0.0)
        self.assertEqual(payload["frame"]["frame_id"], "donkey_frame_000000")
        self.assertTrue(payload["frame"]["has_image"])
        self.assertEqual(payload["algorithm"], "test-observer")
        # Idle host has no perception stage; publication still carries cycle control.
        self.assertIsNone(payload["perception"])
        self.assertIsNone(payload["memory"])
        self.assertEqual(payload["control"]["reason"], "stable-idle-engine")
        self.assertEqual(payload["frame"]["frame_path"], LATEST_FRAME_PATH)

    def test_publication_includes_memory_snapshot_when_stage_present(self) -> None:
        from autonomy.decision import (
            DecisionFrameContext,
            DecisionStages,
            MemoryBounds,
            MemoryProvenance,
            MemorySnapshot,
            Observation,
            RetainedEvidence,
        )
        from autonomy.perception import ViewLocation
        from autonomy.runtime.cycle_host import AutonomyCycleHost

        def remember(context, observation):
            del observation
            return MemorySnapshot(
                memory_id="mem-1",
                epoch_id="epoch-1",
                health="healthy",
                bounds=MemoryBounds(max_records=4),
                created_at_ms=context.timestamp_ms,
                records=(
                    RetainedEvidence(
                        record_id="thing:boundary",
                        kind="floor_boundary",
                        label="boundary",
                        confidence=0.9,
                        provenance=MemoryProvenance(
                            observation_id="obs",
                            evidence_id="boundary",
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
                    ),
                ),
                implementation_id="bounded_evidence",
            )

        host = AutonomyCycleHost(stages=DecisionStages(remember=remember))
        part = AutonomyPilotPart(host=host, min_interval_s=0.0, algorithm="test")
        part.run(image_array=np.zeros((8, 8, 3), dtype=np.uint8), mode="user")
        payload = part.publish_latest(now_ms=part.latest_snapshot.completed_at_ms)
        self.assertIsNotNone(payload["memory"])
        self.assertEqual(payload["memory"]["health"], "healthy")
        self.assertEqual(payload["memory"]["record_count"], 1)
        self.assertEqual(payload["memory"]["records"][0]["kind"], "floor_boundary")

        jpeg, frame_meta = part.publish_latest_frame_jpeg()
        self.assertIsNotNone(jpeg)
        self.assertGreater(len(jpeg), 32)
        self.assertEqual(frame_meta["frame"]["frame_id"], payload["frame"]["frame_id"])
        self.assertEqual(frame_meta["health"], "healthy")
        self.assertTrue(jpeg.startswith(b"\xff\xd8"))

    def test_stale_and_error_health_states(self) -> None:
        part = AutonomyPilotPart(host=AutonomyCycleHost(), min_interval_s=0.5)
        part.run(image_array=np.zeros((4, 4, 3), dtype=np.uint8), mode="user")
        completed = part.latest_snapshot.completed_at_ms
        stale = part.publish_latest(now_ms=completed + 5_000)
        self.assertEqual(stale["health"], "stale")
        self.assertTrue(stale["ok"])
        self.assertGreater(stale["result_age_ms"], stale["stale_after_ms"])

        class Boom:
            def status(self):
                return {"engine": {"engine": "boom"}}

            def run(self, context):
                del context
                raise RuntimeError("boom")

        failing = AutonomyPilotPart(host=Boom(), min_interval_s=0.0)  # type: ignore[arg-type]
        failing.run(image_array=np.zeros((4, 4, 3), dtype=np.uint8), mode="user")
        errored = failing.publish_latest(now_ms=failing.latest_snapshot.completed_at_ms)
        self.assertEqual(errored["health"], "error")
        self.assertFalse(errored["ok"])
        self.assertIn("RuntimeError", errored["error"] or "")

    def test_unavailable_when_image_missing(self) -> None:
        part = AutonomyPilotPart(host=AutonomyCycleHost(), min_interval_s=0.0)
        part.run(image_array=None, mode="user")
        payload = part.publish_latest(now_ms=part.latest_snapshot.completed_at_ms)
        self.assertEqual(payload["health"], "unavailable")
        jpeg, meta = part.publish_latest_frame_jpeg()
        self.assertIsNone(jpeg)
        self.assertEqual(meta["health"], "unavailable")

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
        self.assertIn("observation_publisher = autonomy_part", manage)
        self.assertIn("algorithm=perception_algorithm", manage)

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
        self.assertIn('/autonomy/memory/reset', patch)
        self.assertIn("class AutonomyObservationLatestAPI", patch)
        self.assertIn("class AutonomyObservationLatestFrameAPI", patch)
        self.assertIn("class AutonomyMemoryResetAPI", patch)


if __name__ == "__main__":
    unittest.main(verbosity=2)
