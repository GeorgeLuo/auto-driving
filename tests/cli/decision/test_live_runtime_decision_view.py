from __future__ import annotations
import json
import unittest
from copy import deepcopy
from urllib.request import urlopen
from autonomy.decision_cycle.proposal.inputs import ComponentEnvelope
from tests.cli.decision.decision_surfaces_fixtures import left_obstruction_frame
from tests.cli.decision.live_runtime_decision_view_fixtures import LiveRuntimeDecisionViewFixture


class LiveRuntimeDecisionViewTests(LiveRuntimeDecisionViewFixture, unittest.TestCase):
    def test_serves_exact_decision_image_and_separate_authority_facts(self) -> None:
        stream_frame, expected_image = self._publish_exact_transaction()
        generation = self.server.decision.generation_id
        self.assertIsNotNone(generation)
        with urlopen(
            f"{self.server.url}api/decision/latest?generation={generation}", timeout=1.0
        ) as response:
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            payload = json.loads(response.read().decode("utf-8"))
        self.assertEqual(payload["status"], "current")
        self.assertEqual(payload["decision"]["frame_id"], stream_frame["frame_id"])
        self.assertEqual(payload["current_image"]["frame_id"], stream_frame["frame_id"])
        self.assertFalse(payload["authority"]["proposed_applied"])
        self.assertIn("authorized_output", payload["authority"])
        self.assertIn("host_application", payload["authority"])

        with urlopen(
            f"{self.server.url.rstrip('/')}{payload['current_image']['url']}",
            timeout=1.0,
        ) as response:
            self.assertEqual(response.headers.get_content_type(), "image/png")
            self.assertEqual(response.read(), expected_image)

        with urlopen(
            f"{self.server.url}decision?generation={generation}", timeout=1.0
        ) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers.get_content_type(), "text/html")

        self.assertEqual(self.server.decision.health_payload()["status"], "running")
        self.assertEqual(
            payload["freshness"]["captured_at_ms"],
            stream_frame["timestamp_ms"],
        )
        self.assertNotEqual(
            payload["freshness"]["captured_at_ms"],
            payload["freshness"]["published_at_ms"],
        )

    def test_api_keeps_host_observations_separate_and_attributable(self) -> None:
        raw = left_obstruction_frame()
        frame_id = raw["frame_id"]
        cases = (
            ("absent", None, "unavailable", None),
            (
                "malformed-frame",
                ComponentEnvelope(
                    status="ready", value={"frame_id": 7, "steering": 0.4}
                ),
                "ready",
                0.4,
            ),
            (
                "wrong-frame",
                ComponentEnvelope(
                    status="ready",
                    value={"frame_id": "older-frame", "steering": -0.6},
                ),
                "ready",
                -0.6,
            ),
            (
                "nonzero",
                ComponentEnvelope(
                    status="ready",
                    value={"frame_id": frame_id, "steering": 0.5, "throttle": 0.2},
                ),
                "ready",
                0.5,
            ),
        )
        for index, (name, host_application, expected_status, steering) in enumerate(
            cases
        ):
            with self.subTest(name=name):
                stream_frame = self._accepted_frame(
                    raw=raw,
                    host_application=host_application,
                )
                self._publish_exact_transaction(
                    stream_frame=stream_frame,
                    image_name=f"host-{index}.png",
                )
                host = self._latest_payload()["authority"]["host_application"]
                self.assertEqual(host["status"], expected_status)
                if steering is None:
                    self.assertIsNone(host["value"])
                else:
                    self.assertEqual(host["value"]["steering"], steering)

    def test_exact_retained_evidence_is_machine_visible_and_renderable(self) -> None:
        stream_frame = self._accepted_frame_with_evidence()
        self._publish_exact_transaction(stream_frame=stream_frame)

        payload = self._latest_payload()
        evidence = payload["evidence"]
        self.assertEqual(evidence["status"], "available")
        self.assertEqual(evidence["frame_id"], payload["current_image"]["frame_id"])
        self.assertEqual(evidence["transaction_id"], payload["transaction_id"])
        self.assertEqual(evidence["available_count"], 1)
        projected = evidence["records"][0]
        self.assertEqual(projected["status"], "available")
        self.assertEqual(projected["reason"], "")
        self.assertEqual(
            projected["record"],
            payload["provenance"]["evidence"]["value"][0],
        )

    def test_unmatched_retained_evidence_preserves_origin_without_overlay(
        self,
    ) -> None:
        def older_frame(_raw, record, _thing) -> None:
            record["origin"]["frame_id"] = "frame_older"

        def observation_mismatch(_raw, record, _thing) -> None:
            record["origin"]["observation_id"] = "obs_other"

        def missing_evidence(_raw, _record, thing) -> None:
            thing["thing_id"] = "ev_other"

        def ambiguous_evidence(raw, _record, thing) -> None:
            raw["observation"]["things"].append(deepcopy(thing))

        def provenance_mismatch(_raw, record, _thing) -> None:
            record["origin"]["source_plugin_id"] = "other_plugin"

        def geometry_mismatch(_raw, _record, thing) -> None:
            thing["location"]["bbox_xyxy_norm"] = [0.1, 0.0, 0.3, 0.5]

        def unsupported_geometry(_raw, record, thing) -> None:
            record["location"]["bbox_xyxy_norm"] = None
            thing["location"]["bbox_xyxy_norm"] = None

        cases = (
            ("source_image_unavailable", older_frame),
            ("observation_mismatch", observation_mismatch),
            ("evidence_missing", missing_evidence),
            ("evidence_ambiguous", ambiguous_evidence),
            ("provenance_mismatch", provenance_mismatch),
            ("geometry_mismatch", geometry_mismatch),
            ("unsupported_geometry", unsupported_geometry),
        )
        for index, (expected_reason, mutate) in enumerate(cases):
            with self.subTest(reason=expected_reason):
                stream_frame = self._accepted_frame_with_evidence(mutate)
                self._publish_exact_transaction(
                    stream_frame=stream_frame,
                    image_name=f"evidence-{index}.png",
                )
                payload = self._latest_payload()
                evidence = payload["evidence"]
                self.assertEqual(evidence["status"], "unavailable")
                self.assertEqual(evidence["available_count"], 0)
                projected = evidence["records"][0]
                self.assertEqual(projected["status"], "unavailable")
                self.assertEqual(projected["reason"], expected_reason)
                self.assertIsNone(projected["record"])
                self.assertEqual(
                    projected["origin"],
                    payload["provenance"]["evidence"]["value"][0][
                        "origin"
                    ],
                )
