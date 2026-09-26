import json
import tempfile
import unittest
from pathlib import Path

from scripts.jev_query import (
    TEMPORAL_DURABILITY_QUESTION,
    build_contract_packet,
    build_fixture,
    normalize_response,
)
from scripts.perception.durable_obstacles import local_durability_corrected


class DurableObstacleFixtureTest(unittest.TestCase):
    def test_coherent_fragment_and_jump_are_auditable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = []
            boxes = [[0.10, 0.20, 0.20, 0.35], [0.12, 0.21, 0.22, 0.36], [0.14, 0.22, 0.24, 0.37], [0.70, 0.70, 0.90, 0.90]]
            for i, box in enumerate(boxes):
                things = [{"thing_id": f"d{i}", "bbox_xyxy_norm": box, "confidence": 0.8, "properties": {"flow_support": 0.5, "shape_support": 0.8, "track_event": "new" if i == 0 else "matched"}}]
                if i == 1:
                    things.append({"thing_id": "predicted-only", "bbox_xyxy_norm": box, "confidence": 0.2, "properties": {"flow_support": 0.1, "track_event": "predicted"}})
                rows.append({"position": i, "frame_id": f"f{i}", "timestamp_ms": i * 100, "image_sha256": f"image-{i}", "things": things})
            rows.append({"position": 4, "frame_id": "f4", "timestamp_ms": 1400, "image_sha256": "image-4", "things": []})
            detections = root / "detections.jsonl"
            detections.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
            result = local_durability_corrected(detections, root / "out")
            self.assertGreaterEqual(result["track_count"], 2)
            decisions = json.loads((root / "out" / "decisions.json").read_text())['decisions']
            self.assertIn("durable", {item["verdict"] for item in decisions})
            self.assertTrue(any(item["prediction_count"] > 0 for item in decisions))
            emitted = [json.loads(line) for line in (root / "out" / "emitted-boxes.jsonl").read_text().splitlines()]
            self.assertTrue(all(item["verdict"] if "verdict" in item else True for item in emitted))
            self.assertTrue(all(item["source_status"] == "observed" for item in emitted))
            self.assertTrue(all(item["verdict"] == "durable" for item in emitted))
            self.assertTrue(any(not row["observations"] for row in json.loads((root / "out" / "per-frame.json").read_text())))
            self.assertTrue((root / "out" / "diagnostics.json").exists())

    def test_prediction_cannot_bridge_distant_actual(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = []
            events = [(0.1, "new"), (0.1, "matched"), (0.7, "predicted"), (0.7, "matched")]
            for i, (x, event) in enumerate(events):
                rows.append({"position": i, "frame_id": f"f{i}", "timestamp_ms": i * 100, "image_sha256": f"img-{i}", "things": [{"thing_id": f"d{i}", "bbox_xyxy_norm":[x, .2, x + .1, .3], "confidence": .8, "properties":{"track_event": event, "shape_support": .8, "flow_support": .5}}]})
            source = root / "probe.jsonl"; source.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
            result = local_durability_corrected(source, root / "out")
            decisions = json.loads((root / "out" / "decisions.json").read_text())["decisions"]
            self.assertEqual(result["prediction_count"], 1)
            self.assertFalse(any(d["verdict"] == "durable" and len(d["actual_observation_ids"]) == 3 for d in decisions))

    def test_material_competition_marks_both_tracks_uncertain(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = [
                {"position": 0, "frame_id": "c0", "timestamp_ms": 0, "image_sha256": "c0", "things": [{"thing_id":"a","bbox_xyxy_norm":[.1,.2,.2,.3],"properties":{"track_event":"new","shape_support":.8,"flow_support":.5}},{"thing_id":"b","bbox_xyxy_norm":[.3,.2,.4,.3],"properties":{"track_event":"new","shape_support":.8,"flow_support":.5}}]},
                {"position": 1, "frame_id": "c1", "timestamp_ms": 100, "image_sha256": "c1", "things": [{"thing_id":"one","bbox_xyxy_norm":[.2,.2,.3,.3],"properties":{"track_event":"matched","shape_support":.8,"flow_support":.5}}]},
            ]
            source = root / "competition.jsonl"; source.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
            local_durability_corrected(source, root / "out")
            decisions = json.loads((root / "out" / "decisions.json").read_text())["decisions"]
            self.assertTrue(all(d["verdict"] == "uncertain" for d in decisions))
            self.assertTrue(any(d["material_competition_evidence"] for d in decisions))

    def test_established_track_fork_is_material_and_not_emitted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); rows = []
            for i, boxes in enumerate([[[.30,.2,.40,.3]], [[.30,.2,.40,.3]], [[.30,.2,.40,.3]], [[.29,.2,.39,.3],[.31,.2,.41,.3]]]):
                rows.append({"position": i, "frame_id": f"f{i}", "timestamp_ms": i * 100, "image_sha256": f"fork-{i}", "things": [{"thing_id": f"d{i}-{j}", "bbox_xyxy_norm": b, "confidence": .8, "properties":{"track_event":"new" if i == 0 else "matched", "shape_support": .8, "flow_support": .5}} for j, b in enumerate(boxes)]})
            source = root / "fork.jsonl"; source.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
            local_durability_corrected(source, root / "out")
            decisions = json.loads((root / "out" / "decisions.json").read_text())["decisions"]
            self.assertTrue(any(d["material_competition_evidence"] for d in decisions))
            self.assertTrue(all(d["verdict"] != "durable" for d in decisions if d["material_competition_evidence"]))
            self.assertEqual((root / "out" / "emitted-boxes.jsonl").read_text(), "")


class JevContractBridgeTest(unittest.TestCase):
    def _packet(self, root: Path):
        observations = [
            {
                "observation_id": "f0:0",
                "frame_id": "f0",
                "position": 0,
                "timestamp_ms": 0,
                "image_sha256": "image-0",
                "detection_id": "d0",
                "bbox_xyxy_norm": [0.10, 0.20, 0.20, 0.35],
                "source_status": "observed",
                "is_actual": True,
                "association": {"status": "new"},
            },
            {
                "observation_id": "f1:0",
                "frame_id": "f1",
                "position": 1,
                "timestamp_ms": 100,
                "image_sha256": "image-1",
                "detection_id": "d1",
                "bbox_xyxy_norm": [0.11, 0.20, 0.21, 0.35],
                "source_status": "observed",
                "is_actual": True,
                "association": {"status": "matched"},
            },
        ]
        tracks = {
            "schema_version": "durable_tracks_v2",
            "source_detections_sha256": "source-detections-hash",
            "tracks": [
                {
                    "track_id": "track_0000",
                    "verdict": "durable",
                    "observations": observations,
                    "prediction_count": 0,
                    "held_count": 0,
                    "visible_interval": {"start_frame_id": "f0", "end_frame_id": "f1", "eligible_frame_count": 2, "actual_frame_count": 2, "supported_fraction": 1.0},
                    "metrics": {"local_center_residual": 0.01, "local_scale_residual": 0.01},
                    "failed_checks": [],
                    "checks": {
                        "minimum_independent_observations": {"measured": 2, "threshold": 2, "passed": True},
                        "minimum_time_span": {"measured": 0.1, "threshold": 0.05, "passed": True},
                        "minimum_supported_fraction": {"measured": 1.0, "threshold": 0.5, "passed": True},
                    },
                    "gaps": [],
                    "counterevidence": [],
                    "material_competition_evidence": [],
                },
                {
                    "track_id": "track_0001",
                    "verdict": "uncertain",
                    "observations": [observations[0]],
                    "prediction_count": 0,
                    "held_count": 0,
                    "visible_interval": {"start_frame_id": "f0", "end_frame_id": "f0", "eligible_frame_count": 1, "actual_frame_count": 1, "supported_fraction": 1.0},
                    "metrics": {},
                    "failed_checks": ["minimum_independent_observations"],
                    "checks": {
                        "minimum_independent_observations": {"measured": 1, "threshold": 2, "passed": False},
                        "minimum_time_span": {"measured": 0.0, "threshold": 0.05, "passed": False},
                        "minimum_supported_fraction": {"measured": 1.0, "threshold": 0.5, "passed": True},
                    },
                    "gaps": [],
                    "counterevidence": [{"type": "insufficient_support"}],
                    "material_competition_evidence": [],
                },
            ],
        }
        tracks_path = root / "tracks.json"
        tracks_path.write_text(json.dumps(tracks), encoding="utf-8")
        detections_path = root / "detections.jsonl"
        detections_path.write_text(
            "\n".join(json.dumps({"position": i, "frame_id": f"f{i}", "timestamp_ms": i * 100, "image_sha256": f"image-{i}", "things": [{"thing_id": f"d{i}", "bbox_xyxy_norm": [0.10 + i * 0.01, 0.20, 0.20 + i * 0.01, 0.35], "properties": {"track_event": "observed"}}]}) for i in range(2)) + "\n",
            encoding="utf-8",
        )
        config_path = root / "tracking-config.json"
        config_path.write_text(json.dumps({"schema_version": "fixture-policy"}), encoding="utf-8")
        frozen_path = root / "frozen-inputs.json"
        frozen_path.write_text(json.dumps({"manifest_sha256": "manifest-hash", "config_sha256": "detector-config-hash"}), encoding="utf-8")
        packet, _ = build_contract_packet(
            sequence_id="fixture-sequence",
            tracks_path=tracks_path,
            detections_path=detections_path,
            tracking_config_path=config_path,
            frozen_inputs_path=frozen_path,
        )
        return packet

    def test_packet_and_fixture_preserve_temporal_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            packet = self._packet(Path(tmp))
            self.assertEqual(packet["question"]["text"], TEMPORAL_DURABILITY_QUESTION)
            self.assertFalse(packet["images_provided"])
            self.assertEqual(len(packet["state"]["frames"]), 2)
            fixture_input, fixture_response = build_fixture(packet)
            normalized = normalize_response(
                packet,
                fixture_response,
                decision_source="fixture",
                request_sha256="request-hash",
                raw_response_reference="fixture-response.json",
            )
            results = {item["track_id"]: item for item in normalized["results"]}
            self.assertEqual(results["track_0000"]["verdict"], "durable")
            self.assertEqual(results["track_0001"]["verdict"], "uncertain")
            self.assertEqual(results["track_0001"]["raw_answer"]["verdict"], "durable")
            self.assertIn("minimum_independent_observations", results["track_0001"]["authorization_failures"])
            self.assertEqual(packet["state"]["candidates"][0]["independent_temporal_support"]["unique_image_count"], 2)
            self.assertEqual(fixture_input["candidates"][0]["track_id"], "track_0000")

    def test_unknown_support_id_cannot_authorize_durable(self):
        with tempfile.TemporaryDirectory() as tmp:
            packet = self._packet(Path(tmp))
            normalized = normalize_response(
                packet,
                {"answers": {"track_0000": {"verdict": "durable", "supporting_observation_ids": ["missing"]}}},
                decision_source="fixture",
                request_sha256="request-hash",
                raw_response_reference="response.json",
            )
            result = next(item for item in normalized["results"] if item["track_id"] == "track_0000")
            self.assertEqual(result["verdict"], "uncertain")
            self.assertIn("unknown_observation_id", result["mapping_warnings"])


if __name__ == "__main__":
    unittest.main()
