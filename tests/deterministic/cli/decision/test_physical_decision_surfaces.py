from __future__ import annotations

import io
import json
import threading
import time
import unittest
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

import numpy as np
from PIL import Image

from autonomy.decision_cycle.activation import STEPS, step_activation_path, write_step_activation
from autonomy.decision_cycle.steps import decision_steps
from cli.automa_cli.bundles import controller_bundle_paths
from cli.automa_cli.decision import accept_decision_publication, decision_view_frame
from cli.automa_cli.decision_live import DecisionViewAdapter, _provider_identity
from cli.automa_cli.decision_records import activations_from_payloads
from cli.automa_cli.runtime_view import RuntimeViewServer
from cli.automa_cli.step_activations import replace_metadata
from implementations.decision_cycle.catalog import packaged_activation
from implementations.runtime.picar import AutonomyPilotPart, create_host
from tests.shared.cli.decision.decision_surfaces_fixtures import (
    DecisionSurfaceFixture,
    packaged_identity,
    packaged_decision_steps,
)
from tests.shared.cli.invoke import invoke_automa


class DecisionSurfaceTests(DecisionSurfaceFixture, unittest.TestCase):
    def test_decision_adapter_never_writes_perception(self) -> None:
        now_ms = int(time.time() * 1000)
        normalized = accept_decision_publication(
            self._physical_publication(published_at_ms=now_ms), host_id="piracer", now_ms=now_ms,
        )
        server = RuntimeViewServer(
            host_id="piracer", automation_dir=self.runtime_root / "view",
            run_id=normalized["run_id"], decision_identity=_provider_identity(normalized),
        )
        adapter = DecisionViewAdapter(
            host_id="piracer", base_url="http://host.invalid", view_server=server,
            timeout_s=0.1,
        )
        with patch("cli.automa_cli.decision_live.read_host_telemetry_panel", return_value={}), \
                patch.object(server.perception, "publish_frame") as frame, \
                patch.object(server.perception, "publish_perception") as perception:
            self.assertTrue(adapter.publish_frame(normalized, (b"fixture-jpeg", "image/jpeg")))
        self.assertIsNotNone(server.decision.health_payload()["latest_transaction_id"])
        frame.assert_not_called()
        perception.assert_not_called()

    def test_physical_decision_acceptance_rejects_bounded_unavailable_cases(
        self,
    ) -> None:
        now_ms = 2_000
        publication = self._physical_publication(published_at_ms=now_ms)
        accepted = accept_decision_publication(
            publication,
            host_id="piracer",
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
                        "host_id": "other-vehicle",
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
                    host_id="piracer",
                    now_ms=now_ms,
                )
            self.assertEqual(raised.exception.error, "decision_publication_unavailable")
            self.assertEqual(raised.exception.details.get("reason"), expected_reason)

    def test_physical_decision_is_served_under_the_host_producer_identity(self) -> None:
        now_ms = int(time.time() * 1000)
        normalized = accept_decision_publication(
            self._physical_publication(published_at_ms=now_ms),
            host_id="piracer",
            now_ms=now_ms,
        )
        server = RuntimeViewServer(
            host_id="piracer",
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
            host_id="piracer",
            now_ms=now_ms,
        )
        server = RuntimeViewServer(
            host_id="piracer",
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
                host_id="piracer",
                base_url="http://piracer.invalid:8887",
                view_server=server,
                timeout_s=0.1,
            )
            self.assertTrue(adapter.refresh())

        generation = server.decision.generation_id
        self.assertIsNotNone(generation)
        assert server.url is not None
        with self.assertRaises(HTTPError) as warming:
            urlopen(f"{server.url.rstrip('/')}/api/latest", timeout=1.0)
        self.assertEqual(warming.exception.code, 503)
        warming.exception.close()

        with urlopen(f"{server.url.rstrip('/')}/perception", timeout=1.0) as response:
            perception_page = response.read().decode("utf-8")
        with urlopen(f"{server.url.rstrip('/')}/memory", timeout=1.0) as response:
            memory_page = response.read().decode("utf-8")
        decision_link = f"/decision?generation={generation}"
        self.assertIn(f'href="{decision_link}"', perception_page)
        self.assertIn(f'href="{decision_link}"', memory_page)

    def test_physical_source_to_public_cli_http_fixture_and_expiry(self) -> None:
        host_id = "piracer-fixture"
        steps = packaged_decision_steps()
        part = AutonomyPilotPart(
            host=create_host(steps=decision_steps(activations_from_payloads(steps))),
            host_id=host_id,
            source_id=f"donkeycar:{host_id}",
            decision_activations=steps,
            generation_id=packaged_identity()["generation_id"],
            run_id="donkey-run-http-fixture",
        )
        part.run(image_array=np.zeros((4, 4, 3), dtype=np.uint8), mode="user")
        part.wait_for_cycle()
        assert part.latest_state is not None
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
                if self.path == "/host/status":
                    self._write_json({"ok": True, "drive_mode": "user"})
                    return
                if self.path == "/host/decision/latest":
                    template = part.publish_decision_latest(
                        now_ms=part.latest_state.completed_at_ms
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
        from tests.support.runtime_fixtures import write_host_fixture
        write_host_fixture(self.runtime_root, host_id, provider="picar", url=base_url)
        # The CLI addresses a PiCar by the connection its staging recorded.
        proposal = packaged_activation("proposal")
        write_step_activation(
            step_activation_path(controller_bundle_paths(self.runtime_root / host_id)["runtime_dir"], "proposal"),
            replace_metadata(proposal, {
                **proposal.metadata, "host_id": host_id, "provider": "picar",
                "runtime": {"connection": {"base_url": base_url}},
            }),
        )
        try:
            env = {}
            json_result = invoke_automa(
                "host", "stream", "action", "--id", host_id, "--once", "--json",
                runtime_root=self.runtime_root,
                extra_env=env,
            )
            payload = json.loads(json_result.stdout)
            self.assertEqual(payload["schema"], "host_action_stream_v1")
            self.assertEqual(payload["host_id"], host_id)
            self.assertEqual(payload["run_id"], "donkey-run-http-fixture")
            self.assertEqual(payload["plugins"], steps["action"]["plugins"])
            authority = payload["record"]["authority"]
            self.assertFalse(authority["proposed_applied"])
            self.assertEqual(authority["authorized_output"]["steering"], 0.0)
            self.assertEqual(authority["authorized_output"]["throttle"], 0.0)
            # The host's cycle timing reaches the stream as it published it.
            self.assertEqual([entry["step"] for entry in payload["timing"]["steps"]], list(STEPS))

            text_result = invoke_automa(
                "host", "stream", "action", "--id", host_id, "--once",
                runtime_root=self.runtime_root,
                extra_env=env,
            )
            self.assertIn(f"Action stream: {host_id}", text_result.stdout)
            self.assertIn("Authorized as proposed: false", text_result.stdout)
            self.assertRegex(text_result.stdout, r"Timing: action \d+\.\d{3} ms \(.+\); cycle \d+\.\d{3} ms")

            fixture_state["force_expired"] = True
            expired = invoke_automa(
                "host", "stream", "action", "--id", host_id, "--once", "--json",
                runtime_root=self.runtime_root,
                extra_env=env,
                check=False,
            )
            self.assertEqual(expired.returncode, 2, expired.stderr + expired.stdout)
            unavailable = json.loads(expired.stdout)
            self.assertEqual(unavailable["error"], "decision_publication_unavailable")
            self.assertEqual(unavailable["details"]["reason"], "expired")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2.0)
