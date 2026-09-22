from __future__ import annotations
import json
import io
import threading
import time
import unittest
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import urlopen
from unittest.mock import patch
import numpy as np
from PIL import Image
from autonomy.runtime.cycle_host import AutonomyCycleHost
from autonomy.runtime.manager import AutonomyManager
from cli.automa_cli.decision_live import PhysicalDecisionViewAdapter
from cli.automa_cli.decision import (
    ADAPTER_ENGINE_SPEC,
    DECISION_ENGINES,
    ENGINE_ID,
    accept_physical_decision_publication,
    physical_decision_view_frame,
)
from cli.automa_cli.runtime_view import RuntimeViewServer
from implementations.runtime.donkeycar import AutonomyPilotPart
from tests.support.cli_runner import run_automa
from tests.cli.decision.shadow_decision_surfaces_fixtures import (
    ShadowDecisionSurfaceFixture,
)


class ShadowDecisionSurfaceTests(ShadowDecisionSurfaceFixture, unittest.TestCase):
    def test_physical_decision_acceptance_rejects_bounded_unavailable_cases(
        self,
    ) -> None:
        now_ms = 2_000
        publication = self._physical_publication(published_at_ms=now_ms)
        accepted = accept_physical_decision_publication(
            publication,
            vehicle_id="piracer",
            now_ms=now_ms,
        )
        self.assertEqual(accepted["source_id"], "donkeycar:piracer")
        self.assertEqual(accepted["generation_id"], f"{ENGINE_ID}:1000")
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
                accept_physical_decision_publication(
                    candidate,
                    vehicle_id="piracer",
                    now_ms=now_ms,
                )
            self.assertEqual(raised.exception.error, "physical_decision_unavailable")
            self.assertEqual(raised.exception.details.get("reason"), expected_reason)

    def test_physical_decision_uses_shared_runtime_view_without_local_pid(self) -> None:
        now_ms = int(time.time() * 1000)
        normalized = accept_physical_decision_publication(
            self._physical_publication(published_at_ms=now_ms),
            vehicle_id="piracer",
            now_ms=now_ms,
        )
        server = RuntimeViewServer(
            vehicle_id="piracer",
            automation_dir=self.runtime_root / "piracer" / "physical_observation",
            port=0,
            run_id=normalized["run_id"],
            decision_provider_identity={
                "vehicle_id": normalized["vehicle_id"],
                "source_id": normalized["source_id"],
                "run_id": normalized["run_id"],
                "activation_engine_id": normalized["activation_engine_id"],
                "activation_activated_at_ms": normalized["activation_activated_at_ms"],
                "producer_generation_id": normalized["generation_id"],
            },
        ).start()
        self.addCleanup(server.stop)

        self.assertIsNone(server.worker_pid)
        stream_frame = physical_decision_view_frame(normalized)
        self.assertTrue(
            server.decision.publish_provider_transaction(
                stream_frame=stream_frame,
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
        self.assertNotIn("worker_pid", payload["identity"])
        self.assertEqual(payload["current_image"]["frame_id"], "frame_001")
        self.assertEqual(payload["authority"]["proposed_applied"], False)
        with urlopen(
            f"{server.url.rstrip('/')}{payload['current_image']['url']}",
            timeout=1.0,
        ) as response:
            self.assertEqual(response.read(), b"physical-fixture-jpeg")

    def test_physical_live_adapter_keeps_all_views_in_one_session(self) -> None:
        now_ms = int(time.time() * 1000)
        normalized = accept_physical_decision_publication(
            self._physical_publication(published_at_ms=now_ms),
            vehicle_id="piracer",
            now_ms=now_ms,
        )
        server = RuntimeViewServer(
            vehicle_id="piracer",
            automation_dir=self.runtime_root / "piracer" / "physical_observation",
            port=0,
            run_id=normalized["run_id"],
            decision_provider_identity={
                "vehicle_id": normalized["vehicle_id"],
                "source_id": normalized["source_id"],
                "run_id": normalized["run_id"],
                "activation_engine_id": normalized["activation_engine_id"],
                "activation_activated_at_ms": normalized["activation_activated_at_ms"],
                "producer_generation_id": normalized["generation_id"],
            },
        ).start()
        self.addCleanup(server.stop)

        image_buffer = io.BytesIO()
        Image.new("RGB", (40, 30), (20, 80, 150)).save(image_buffer, format="JPEG")
        with patch(
            "cli.automa_cli.decision_live._accepted_pair",
            return_value=(normalized, (image_buffer.getvalue(), "image/jpeg")),
        ):
            adapter = PhysicalDecisionViewAdapter(
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
        self.assertEqual(perception_payload["memory"]["record_count"], 1)
        self.assertIsNotNone(perception_payload["perception"])

        with urlopen(f"{server.url.rstrip('/')}/perception", timeout=1.0) as response:
            perception_page = response.read().decode("utf-8")
        with urlopen(f"{server.url.rstrip('/')}/memory", timeout=1.0) as response:
            memory_page = response.read().decode("utf-8")
        decision_link = f"/decision?generation={generation}"
        self.assertIn(f'href="{decision_link}"', perception_page)
        self.assertIn(f'href="{decision_link}"', memory_page)

    def test_physical_source_to_public_cli_http_fixture_and_expiry(self) -> None:
        vehicle_id = "piracer-fixture"
        manager = AutonomyManager(
            default_engine_spec=ADAPTER_ENGINE_SPEC,
            default_engine_config=DECISION_ENGINES[ENGINE_ID]["engine_config"],
        )
        part = AutonomyPilotPart(
            host=AutonomyCycleHost(manager=manager),
            min_interval_s=5.0,
            vehicle_id=vehicle_id,
            source_id=f"donkeycar:{vehicle_id}",
            activation_engine_id=ENGINE_ID,
            activation_activated_at_ms=1_000,
            activation_engine_config=DECISION_ENGINES[ENGINE_ID]["engine_config"],
            generation_id=f"{ENGINE_ID}:1000",
            run_id="donkey-run-http-fixture",
        )
        part.run(image_array=np.zeros((4, 4, 3), dtype=np.uint8), mode="user")
        assert part.latest_snapshot is not None
        # CLI subprocess wall-clock now_ms is independent of this fixture. Stamp
        # published_at_ms at request time so current acceptance does not depend
        # on cold-start beating stale_after_ms, while expiry remains explicit.
        fixture_state = {"force_expired": False}

        class FixtureHandler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):  # noqa: A003, ANN001
                del format, args

            def _write_json(self, payload: dict, *, status: int = 200) -> None:
                body = json.dumps(payload, sort_keys=True).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                if self.path == "/autonomy/status":
                    self._write_json({"ok": True, "drive_mode": "user"})
                    return
                if self.path == "/autonomy/decision/latest":
                    template = part.publish_decision_latest(
                        now_ms=part.latest_snapshot.completed_at_ms
                    )
                    if not template.get("ok") or not isinstance(
                        template.get("decision"), dict
                    ):
                        self._write_json(template, status=503)
                        return
                    now_ms = int(time.time() * 1000)
                    stale_after = int(template["stale_after_ms"])
                    age_ms = stale_after + 1 if fixture_state["force_expired"] else 0
                    decision = deepcopy(template["decision"])
                    decision["published_at_ms"] = now_ms - age_ms
                    payload = {
                        "schema": template["schema"],
                        "ok": True,
                        "status": "ready",
                        "reason": "",
                        "read_at_ms": now_ms,
                        "result_age_ms": age_ms,
                        "stale_after_ms": stale_after,
                        "decision": decision,
                    }
                    self._write_json(payload, status=200)
                    return
                self._write_json({"ok": False, "error": "not found"}, status=404)

        server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        thread = threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_port}"
        try:
            env = {"PIRACER_BASE_URL": base_url, "PIRACER_ID": vehicle_id}
            json_result = run_automa(
                "vehicles",
                "stream",
                "decision",
                "--id",
                vehicle_id,
                "--once",
                "--json",
                runtime_root=self.runtime_root,
                extra_env=env,
            )
            payload = json.loads(json_result.stdout)
            self.assertEqual(
                payload["schema"], "automa_physical_decision_publication_v0"
            )
            self.assertTrue(payload["accepted"])
            self.assertEqual(payload["provider"], "picar")
            self.assertEqual(
                payload["decision"]["source_id"], f"donkeycar:{vehicle_id}"
            )
            self.assertEqual(payload["decision"]["run_id"], "donkey-run-http-fixture")
            authority = payload["decision"]["cycle"]["authority"]
            self.assertFalse(authority["proposed_applied"])
            self.assertEqual(authority["authorized_output"]["steering"], 0.0)
            self.assertEqual(authority["authorized_output"]["throttle"], 0.0)

            text_result = run_automa(
                "vehicles",
                "stream",
                "decision",
                "--id",
                vehicle_id,
                "--once",
                runtime_root=self.runtime_root,
                extra_env=env,
            )
            self.assertIn(f"Source: donkeycar:{vehicle_id}", text_result.stdout)
            self.assertIn("proposed_applied=false", text_result.stdout)

            fixture_state["force_expired"] = True
            expired = run_automa(
                "vehicles",
                "stream",
                "decision",
                "--id",
                vehicle_id,
                "--once",
                "--json",
                runtime_root=self.runtime_root,
                extra_env=env,
                check=False,
            )
            self.assertEqual(expired.returncode, 2, expired.stderr + expired.stdout)
            unavailable = json.loads(expired.stdout)
            self.assertEqual(unavailable["error"], "physical_decision_unavailable")
            self.assertEqual(unavailable["details"]["reason"], "expired")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2.0)
