from __future__ import annotations
import json
import io
import time
import unittest
from copy import deepcopy
from urllib.request import urlopen
from unittest.mock import patch
from PIL import Image
from cli.automa_cli.decision_live import DecisionViewAdapter, _provider_identity
from cli.automa_cli.memory_report import plugin_states
from cli.automa_cli.decision import (
    accept_decision_publication,
    decision_view_frame,
)
from cli.automa_cli.runtime_view import RuntimeViewServer
from tests.shared.cli.decision.decision_surfaces_fixtures import DecisionSurfaceFixture, packaged_identity


class DecisionSurfaceTests(DecisionSurfaceFixture, unittest.TestCase):
    def test_physical_decision_acceptance_rejects_bounded_unavailable_cases(
        self,
    ) -> None:
        now_ms = 2_000
        publication = self._physical_publication(published_at_ms=now_ms)
        accepted = accept_decision_publication(
            publication,
            vehicle_id="piracer",
            now_ms=now_ms,
        )
        self.assertEqual(accepted["source_id"], "donkeycar:piracer")
        self.assertEqual(accepted["generation_id"], packaged_identity()["generation_id"])
        self.assertNotIn("producer_pid", accepted["decision"])

        unavailable_cases: list[tuple[str, object]] = [
            ("missing", None),
            ("incomplete", {"schema": "automa_physical_decision_publication_v0"}),
            (
                "mismatched",
                {
                    **deepcopy(publication),
                    "decision": {
                        **deepcopy(publication["decision"]),
                        "vehicle_id": "other-vehicle",
                    },
                },
            ),
            ("future_dated", self._physical_publication(published_at_ms=now_ms + 1)),
            ("expired", self._physical_publication(published_at_ms=now_ms - 1_001)),
            (
                "reset",
                {
                    **deepcopy(publication),
                    "ok": False,
                    "status": "unavailable",
                    "reason": "reset",
                    "decision": None,
                },
            ),
            (
                "failed_step",
                {
                    **deepcopy(publication),
                    "ok": False,
                    "status": "unavailable",
                    "reason": "failed_step",
                    "decision": None,
                },
            ),
        ]
        for expected_reason, candidate in unavailable_cases:
            with self.subTest(reason=expected_reason), self.assertRaises(
                Exception
            ) as raised:
                accept_decision_publication(
                    candidate,
                    vehicle_id="piracer",
                    now_ms=now_ms,
                )
            self.assertEqual(raised.exception.error, "decision_publication_unavailable")
            self.assertEqual(raised.exception.details.get("reason"), expected_reason)

    def test_physical_decision_is_served_under_the_host_producer_identity(self) -> None:
        now_ms = int(time.time() * 1000)
        normalized = accept_decision_publication(
            self._physical_publication(published_at_ms=now_ms),
            vehicle_id="piracer",
            now_ms=now_ms,
        )
        server = RuntimeViewServer(
            vehicle_id="piracer",
            automation_dir=self.runtime_root / "piracer" / "runtime_view",
            port=0,
            run_id=normalized["run_id"],
            decision_identity=_provider_identity(normalized),
        ).start()
        self.addCleanup(server.stop)

        self.assertTrue(
            server.decision.publish(
                report=decision_view_frame(normalized),
                frame_record={
                    "frame_id": normalized["frame_id"],
                    "frame_index": normalized["frame_index"],
                    "captured_at_ms": normalized["timestamp_ms"],
                    "run_id": normalized["run_id"],
                },
                image=(b"physical-fixture-jpeg", "image/jpeg"),
            )
        )

        generation = server.decision.generation_id
        self.assertIsNotNone(generation)
        assert server.url is not None
        with urlopen(
            f"{server.url.rstrip('/')}/decision?generation={generation}",
            timeout=1.0,
        ) as response:
            self.assertEqual(response.status, 200)
        with urlopen(
            f"{server.url.rstrip('/')}/api/decision/latest?generation={generation}",
            timeout=1.0,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(payload["identity"]["source_id"], "donkeycar:piracer")
        self.assertEqual(payload["current_image"]["frame_id"], "frame_001")
        self.assertEqual(payload["authority"]["proposed_applied"], False)
        with urlopen(
            f"{server.url.rstrip('/')}{payload['current_image']['url']}",
            timeout=1.0,
        ) as response:
            self.assertEqual(response.read(), b"physical-fixture-jpeg")

    def test_physical_live_adapter_keeps_all_views_in_one_session(self) -> None:
        now_ms = int(time.time() * 1000)
        normalized = accept_decision_publication(
            self._physical_publication(published_at_ms=now_ms),
            vehicle_id="piracer",
            now_ms=now_ms,
        )
        server = RuntimeViewServer(
            vehicle_id="piracer",
            automation_dir=self.runtime_root / "piracer" / "runtime_view",
            port=0,
            run_id=normalized["run_id"],
            decision_identity=_provider_identity(normalized),
        ).start()
        self.addCleanup(server.stop)

        image_buffer = io.BytesIO()
        Image.new("RGB", (40, 30), (20, 80, 150)).save(image_buffer, format="JPEG")
        with patch(
            "cli.automa_cli.decision_live._accepted_pair",
            return_value=(normalized, (image_buffer.getvalue(), "image/jpeg")),
        ):
            adapter = DecisionViewAdapter(
                vehicle_id="piracer",
                base_url="http://piracer.invalid:8887",
                view_server=server,
                timeout_s=0.1,
            )
            self.assertTrue(adapter.refresh())

        generation = server.decision.generation_id
        self.assertIsNotNone(generation)
        assert server.url is not None
        with urlopen(f"{server.url.rstrip('/')}/api/latest", timeout=1.0) as response:
            perception_payload = json.loads(response.read().decode("utf-8"))
        self.assertEqual(perception_payload["frame"]["frame_id"], "frame_001")
        self.assertEqual(perception_payload["memory"]["evidence_publisher"], "decision_evidence")
        self.assertEqual(
            dict(plugin_states(perception_payload["memory"]))["decision_evidence"]["record_count"],
            1,
        )
        self.assertIsNotNone(perception_payload["perception"])

        with urlopen(f"{server.url.rstrip('/')}/perception", timeout=1.0) as response:
            perception_page = response.read().decode("utf-8")
        with urlopen(f"{server.url.rstrip('/')}/memory", timeout=1.0) as response:
            memory_page = response.read().decode("utf-8")
        decision_link = f"/decision?generation={generation}"
        self.assertIn(f'href="{decision_link}"', perception_page)
        self.assertIn(f'href="{decision_link}"', memory_page)

