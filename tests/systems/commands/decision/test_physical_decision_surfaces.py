from __future__ import annotations
import json
import threading
import time
import unittest
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import numpy as np
from autonomy.decision_cycle.activation import step_activation_path, write_step_activation
from autonomy.decision_cycle.steps import decision_steps
from cli.automa_cli.bundles import controller_bundle_paths
from cli.automa_cli.decision_records import activations_from_payloads
from cli.automa_cli.step_activations import replace_metadata
from implementations.decision_cycle.catalog import packaged_activation
from implementations.runtime.picar import AutonomyPilotPart, create_host
from tests.support.cli_runner import run_automa
from tests.shared.cli.decision.decision_surfaces_fixtures import (
    DecisionSurfaceFixture,
    packaged_decision_steps,
    packaged_identity,
)


class DecisionSurfaceTests(DecisionSurfaceFixture, unittest.TestCase):


    def test_physical_source_to_public_cli_http_fixture_and_expiry(self) -> None:
        vehicle_id = "piracer-fixture"
        steps = packaged_decision_steps()
        part = AutonomyPilotPart(
            host=create_host(steps=decision_steps(activations_from_payloads(steps))),
            interval_s=5.0,
            vehicle_id=vehicle_id,
            source_id=f"donkeycar:{vehicle_id}",
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
                if self.path == "/autonomy/status":
                    self._write_json({"ok": True, "drive_mode": "user"})
                    return
                if self.path == "/autonomy/decision/latest":
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
        # The CLI addresses a PiCar by the connection its staging recorded.
        proposal = packaged_activation("proposal")
        write_step_activation(
            step_activation_path(controller_bundle_paths(self.runtime_root / vehicle_id)["runtime_dir"], "proposal"),
            replace_metadata(proposal, {
                **proposal.metadata, "vehicle_id": vehicle_id, "provider": "picar",
                "runtime": {"connection": {"base_url": base_url}},
            }),
        )
        try:
            env = {}
            json_result = run_automa(
                "vehicles", "stream", "action", "--id", vehicle_id, "--once", "--json",
                runtime_root=self.runtime_root,
                extra_env=env,
            )
            payload = json.loads(json_result.stdout)
            self.assertEqual(payload["schema"], "vehicle_action_stream_v1")
            self.assertEqual(payload["vehicle_id"], vehicle_id)
            self.assertEqual(payload["run_id"], "donkey-run-http-fixture")
            self.assertEqual(payload["plugins"], steps["action"]["plugins"])
            authority = payload["record"]["authority"]
            self.assertFalse(authority["proposed_applied"])
            self.assertEqual(authority["authorized_output"]["steering"], 0.0)
            self.assertEqual(authority["authorized_output"]["throttle"], 0.0)

            text_result = run_automa(
                "vehicles", "stream", "action", "--id", vehicle_id, "--once",
                runtime_root=self.runtime_root,
                extra_env=env,
            )
            self.assertIn(f"Action stream: {vehicle_id}", text_result.stdout)
            self.assertIn("Authorized as proposed: false", text_result.stdout)

            fixture_state["force_expired"] = True
            expired = run_automa(
                "vehicles", "stream", "action", "--id", vehicle_id, "--once", "--json",
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
