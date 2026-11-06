from __future__ import annotations

import io
import json
import threading
import time
import unittest
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.request import urlopen

import numpy as np
from PIL import Image

from autonomy.decision_cycle.activation import STEPS, step_activation_path, write_step_activation
from autonomy.decision_cycle.steps import decision_steps
from cli.automa_cli.releases.paths import controller_bundle_paths
from cli.automa_cli.hosts.publications.decision import accept_decision_publication
from cli.automa_cli.hosts.views.decision_live import DecisionViewAdapter
from cli.automa_cli.hosts.publications.decision_records import activations_from_payloads
from cli.automa_cli.frontend.server import RuntimeViewServer
from cli.automa_cli.workbench.runner import ImageReplayRunner
from cli.automa_cli.hosts.step_activations import replace_metadata
from implementations.decision_cycle.catalog import packaged_activation
from implementations.runtime.picar import AutonomyPilotPart, create_host
from tests.shared.cli.decision.decision_surfaces_fixtures import (
    DecisionSurfaceFixture,
    packaged_identity,
    packaged_decision_steps,
)
from tests.shared.cli.invoke import invoke_automa


class DecisionSurfaceTests(DecisionSurfaceFixture, unittest.TestCase):
    def test_decision_adapter_publishes_the_same_record_for_every_page(self) -> None:
        now = int(time.time() * 1000)
        normalized = accept_decision_publication(self._physical_publication(published_at_ms=now), host_name="piracer", now_ms=now)
        server = RuntimeViewServer(host_name="piracer", automation_dir=None, port=0, replay_runner_factory=ImageReplayRunner).start()
        self.addCleanup(server.stop)
        buffer = io.BytesIO()
        Image.new("RGB", (4, 4)).save(buffer, "JPEG")
        jpeg = buffer.getvalue()
        adapter = DecisionViewAdapter(host_name="piracer", base_url="http://host.invalid", view_server=server, timeout_s=0.1)
        with patch("cli.automa_cli.hosts.views.decision_live.read_host_telemetry_panel", return_value={"status": "unavailable"}):
            self.assertTrue(adapter.publish_frame(normalized, (jpeg, "image/jpeg")))
        with urlopen(f"{server.url}api/latest", timeout=1) as response:
            payload = json.loads(response.read())
        self.assertEqual(payload["report"], normalized["report"].to_dict())
        self.assertEqual(payload["host_telemetry"], {"status": "unavailable"})
        with urlopen(f"{server.url.rstrip('/')}{payload['image']['url']}", timeout=1) as response:
            self.assertEqual(response.read(), jpeg)
        for page in ("perception", "memory", "decision"):
            with urlopen(f"{server.url}{page}", timeout=1) as response:
                self.assertEqual(response.status, 200)

    def test_physical_decision_acceptance_rejects_bounded_unavailable_cases(
        self,
    ) -> None:
        now_ms = 2_000
        publication = self._physical_publication(published_at_ms=now_ms)
        accepted = accept_decision_publication(
            publication,
            host_name="piracer",
            now_ms=now_ms,
        )
        self.assertEqual(accepted["source_id"], "donkeycar:piracer")
        self.assertEqual(accepted["generation_id"], packaged_identity()["generation_id"])
        self.assertNotIn("decision", accepted)
        self.assertEqual(accepted["publication"]["report"], accepted["report"].to_dict())
        self.assertNotIn("producer_pid", accepted["report"].to_dict())

        for name, candidate in (
            ("future_dated", self._physical_publication(published_at_ms=now_ms + 1)),
            ("expired", self._physical_publication(published_at_ms=now_ms - 1_001)),
        ):
            with self.subTest(reason=name):
                readable = accept_decision_publication(
                    candidate, host_name="piracer", now_ms=now_ms,
                )
                self.assertEqual(readable["frame_id"], "frame_001")
                self.assertEqual(readable["report"].status, "ok")
                self.assertIsNone(readable["report"].error)

        unavailable_cases: list[tuple[str, object]] = [
            ("missing", None),
            ("incomplete", {"schema": "automa_physical_decision_publication"}),
            (
                "mismatched",
                {
                    **deepcopy(publication),
                    "report": {
                        **deepcopy(publication["report"]),
                        "host_name": "other-vehicle",
                    },
                },
            ),
            (
                "missing",
                {
                    **deepcopy(publication),
                    "ok": False,
                    "health": "absent",
                    "report": None,
                },
            ),
        ]
        for expected_reason, candidate in unavailable_cases:
            with self.subTest(reason=expected_reason), self.assertRaises(
                Exception
            ) as raised:
                accept_decision_publication(
                    candidate,
                    host_name="piracer",
                    now_ms=now_ms,
                )
            self.assertEqual(raised.exception.error_code, "decision_publication_unavailable")
            self.assertEqual(raised.exception.details.get("reason"), expected_reason)

    def test_physical_source_to_public_cli_http_fixture_and_expiry(self) -> None:
        host_name = "piracer-fixture"
        steps = packaged_decision_steps()
        part = AutonomyPilotPart(
            host=create_host(steps=decision_steps(activations_from_payloads(steps))),
            host_name=host_name,
            source_id=f"donkeycar:{host_name}",
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
                if self.path == "/host/observation/latest":
                    template = part.publish_latest(
                        now_ms=part.latest_state.completed_at_ms
                    )
                    report = template.get("report")
                    if not isinstance(report, dict):
                        self._write_json(template, status=503)
                        return
                    now_ms = int(time.time() * 1000)
                    stale_after = int(template["stale_after_ms"])
                    age_ms = stale_after + 1 if fixture_state["force_expired"] else 0
                    report = deepcopy(report)
                    report["published_at_ms"] = now_ms - age_ms
                    report["values"]["source_frame"]["completed_at_ms"] = now_ms - age_ms
                    payload = {
                        **template,
                        "ok": True,
                        "health": "stale" if fixture_state["force_expired"] else template.get("health"),
                        "read_at_ms": now_ms,
                        "result_age_ms": age_ms,
                        "stale_after_ms": stale_after,
                        "report": report,
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
        write_host_fixture(self.runtime_root, host_name, provider="picar", url=base_url)
        # The CLI addresses a PiCar by the connection its staging recorded.
        proposal = packaged_activation("proposal")
        write_step_activation(
            step_activation_path(controller_bundle_paths(self.runtime_root / host_name)["runtime_dir"], "proposal"),
            replace_metadata(proposal, {
                **proposal.metadata, "host_name": host_name, "provider": "picar",
                "runtime": {"connection": {"base_url": base_url}},
            }),
        )
        try:
            env = {}
            json_result = invoke_automa(
                "plugins", "stream", "--step", "action", "--host-name", host_name, "--once", "--json",
                runtime_root=self.runtime_root,
                extra_env=env,
            )
            payload = json.loads(json_result.stdout)
            self.assertEqual(payload["schema"], "host_action_stream")
            self.assertEqual(payload["host_name"], host_name)
            self.assertEqual(payload["run_id"], "donkey-run-http-fixture")
            self.assertEqual(payload["plugins"], steps["action"]["plugins"])
            authority = payload["record"]["authority"]
            self.assertFalse(authority["proposed_applied"])
            self.assertEqual(authority["authorized_output"]["steering"], 0.0)
            self.assertEqual(authority["authorized_output"]["throttle"], 0.0)
            # The host's cycle timing reaches the stream as it published it.
            self.assertEqual([entry["step"] for entry in payload["timing"]["steps"]], list(STEPS))

            text_result = invoke_automa(
                "plugins", "stream", "--step", "action", "--host-name", host_name, "--once",
                runtime_root=self.runtime_root,
                extra_env=env,
            )
            self.assertIn(f"Action stream: {host_name}", text_result.stdout)
            self.assertIn("Authorized as proposed: false", text_result.stdout)
            self.assertRegex(text_result.stdout, r"Timing: action \d+\.\d{3} ms \(.+\); cycle \d+\.\d{3} ms")

            fixture_state["force_expired"] = True
            expired = invoke_automa(
                "plugins", "stream", "--step", "action", "--host-name", host_name, "--once", "--json",
                runtime_root=self.runtime_root,
                extra_env=env,
                check=False,
            )
            self.assertEqual(expired.returncode, 0, expired.stderr + expired.stdout)
            stale = json.loads(expired.stdout)
            self.assertEqual(stale["schema"], "host_action_stream")
            self.assertEqual(stale["frame_id"], payload["frame_id"])
            self.assertEqual(stale["record"], payload["record"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2.0)
