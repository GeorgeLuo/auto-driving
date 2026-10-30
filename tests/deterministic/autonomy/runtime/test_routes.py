"""The shared runtime HTTP contract, served over the shared frame loop."""
from __future__ import annotations

import base64
import io
import json
import threading
import unittest
from dataclasses import replace
from unittest.mock import patch

import numpy as np
from PIL import Image

from autonomy.decision_cycle.steps import decision_steps
from autonomy.runtime.cycle_host import AutonomyCycleHost
from autonomy.runtime.frame_loop import FrameLoop
from autonomy.runtime.routes import RUNTIME_PATH, RuntimeRoutes
from tests.deterministic.cli.automation_pipeline.cadence_fixtures import GatedPerception


class _Target:
    def acquire(self):
        return None

    def write(self, control):
        return {"boundary": "test", "transport": "in_process"}

    def release(self):
        pass


def _routes(steps=None, **kwargs) -> tuple[FrameLoop, RuntimeRoutes]:
    loop_options = {}
    if "decision_activations" in kwargs:
        loop_options["decision_activations"] = kwargs.pop("decision_activations")
    loop = FrameLoop(
        host=AutonomyCycleHost(steps=steps or decision_steps(), target=_Target()),
        host_name="test-car", runtime="test", run_id="test-run",
        **loop_options,
    )
    return loop, RuntimeRoutes(loop, **kwargs)


def _json(response):
    status, headers, body = response
    return status, json.loads(body)


class RuntimeRoutesTests(unittest.TestCase):
    def test_status_reports_the_execution_mode_and_host_run(self) -> None:
        _, routes = _routes()
        status, payload = _json(routes.handle("GET", "/host/status"))
        self.assertEqual(status, 200)
        self.assertEqual(payload["mode"], "manual")
        self.assertEqual(payload["host_run_id"], "test-run")
        self.assertEqual(payload["host_name"], "test-car")
        self.assertEqual(payload["session"]["status"], "pre_start")
        self.assertNotIn("drive_mode", payload)

    def test_runtime_commands_start_and_stop_the_shared_session(self) -> None:
        _, routes = _routes()
        body = json.dumps({"command": "run", "configuration": {"mode": "observe_only", "num_decisions": 2}})
        status, payload = _json(routes.handle("POST", "/host/runtime", body.encode()))
        self.assertEqual(status, 200)
        self.assertEqual(payload["session"]["status"], "running")
        self.assertEqual(_json(routes.handle("GET", "/host/status"))[1]["mode"], "observe_only")
        status, payload = _json(routes.handle("POST", "/host/runtime", b'{"command": "stop"}'))
        self.assertEqual(payload["session"]["status"], "stopped")

    def test_bad_requests_return_the_reason(self) -> None:
        _, routes = _routes()
        self.assertEqual(
            _json(routes.handle("POST", "/host/runtime", b'{"command": "fly"}')),
            (400, {"ok": False, "error": "unknown runtime command 'fly'"}),
        )
        status, payload = _json(routes.handle("POST", "/host/runtime", b"{"))
        self.assertEqual(status, 400)
        self.assertIn("not JSON", payload["error"])
        self.assertEqual(_json(routes.handle("GET", "/host/nothing"))[0], 404)
        self.assertEqual(_json(routes.handle("POST", "/host/status"))[0], 405)
        self.assertEqual(_json(routes.handle("POST", "/host/mode", b"{}"))[0], 404)
        self.assertFalse(routes.handles("/host/memory/reset"))
        self.assertEqual(_json(routes.handle("POST", "/host/memory/reset", b"{}"))[0], 404)

    def test_run_without_named_settings_is_refused(self) -> None:
        _, routes = _routes()
        for request, reason in (
            ({"command": "run"}, "configuration must name mode, num_decisions"),
            ({"command": "run", "configuration": {"num_decisions": 3}},
             "configuration must name mode"),
        ):
            with self.subTest(request=request):
                status, payload = _json(routes.handle("POST", RUNTIME_PATH, json.dumps(request).encode()))
                self.assertEqual((status, payload["error"]), (400, reason))
                self.assertEqual(_json(routes.handle("GET", RUNTIME_PATH))[1]["session"]["status"], "pre_start")

    def test_restart_stops_then_replaces_the_process(self) -> None:
        restarted = threading.Event()
        _, routes = _routes(on_restart=restarted.set)
        status, payload = _json(routes.handle("POST", "/host/runtime", b'{"command": "restart"}'))
        self.assertEqual(status, 200)
        self.assertTrue(restarted.wait(2.0))
        _, fixed = _routes()
        self.assertEqual(_json(fixed.handle("POST", "/host/runtime", b'{"command": "restart"}'))[0], 400)

    def test_submitted_frames_publish_observation_camera_and_jpeg(self) -> None:
        loop, routes = _routes()
        self.assertEqual(_json(routes.handle("GET", "/host/observation/latest"))[1]["health"], "warming")
        loop.submit(np.zeros((4, 6, 3), dtype=np.uint8), frame_id="sim_frame_000042", frame_index=42,
                    metadata={"simulator_frame_index": 42})
        loop.wait_for_cycle()
        status, payload = _json(routes.handle("GET", "/host/observation/latest"))
        self.assertEqual(status, 200)
        self.assertEqual(payload["frame"]["frame_id"], "sim_frame_000042")
        self.assertEqual(payload["mode"], "manual")
        status, headers, body = routes.handle("GET", "/host/observation/latest/frame.jpg")
        self.assertEqual((status, headers["Content-Type"], headers["X-Frame-Id"]), (200, "image/jpeg", "sim_frame_000042"))
        self.assertTrue(body.startswith(b"\xff\xd8"))
        status, headers, _ = routes.handle("HEAD", "/host/camera/latest/frame.jpg")
        self.assertEqual((status, headers["X-Frame-Index"]), (200, "42"))
        self.assertEqual(_json(routes.handle("GET", "/host/camera/latest"))[1]["perception_state"], "matched")

    def test_transaction_paths_are_handled_and_reject_write_methods(self):
        _, routes = _routes()
        for path in ["/host/observation/transaction", "/host/decision/transaction"]:
            self.assertTrue(routes.handles(path))
            self.assertEqual(routes.handle("POST", path)[0], 405)

    def test_observation_transaction_warms_without_an_image(self) -> None:
        _, routes = _routes()
        status, transaction = _json(routes.handle("GET", "/host/observation/transaction"))
        self.assertEqual(status, 503)
        self.assertEqual(transaction["publication"]["health"], "warming")
        self.assertIsNone(transaction["image_base64"])

    def test_observation_transaction_keeps_its_image_when_a_new_cycle_finishes(self) -> None:
        loop, routes = _routes()
        loop.submit(np.zeros((4, 6, 3), dtype=np.uint8), frame_id="first", frame_index=1)
        loop.wait_for_cycle()
        encode = FrameLoop.frame_jpeg

        def advance_before_encoding(frame):
            loop.submit(np.full((4, 6, 3), 255, dtype=np.uint8), frame_id="second", frame_index=2)
            loop.wait_for_cycle()
            return encode(frame)

        with patch.object(FrameLoop, "frame_jpeg", side_effect=advance_before_encoding):
            status, transaction = _json(routes.handle("GET", "/host/observation/transaction"))
        self.assertEqual(status, 200, transaction)
        self.assertEqual(transaction["schema"], "automa_observation_transaction")
        self.assertEqual(transaction["publication"]["frame"]["frame_id"], "first")
        self.assertEqual(transaction["image_frame_id"], "first")
        self.assertEqual(transaction["image_content_type"], "image/jpeg")
        with Image.open(io.BytesIO(base64.b64decode(transaction["image_base64"]))) as image:
            self.assertEqual(image.getpixel((0, 0)), (0, 0, 0))
        self.assertEqual(loop.publish_latest()["frame"]["frame_id"], "second")

    def test_observation_transaction_reports_encoding_failure(self) -> None:
        loop, routes = _routes()
        loop.submit(np.zeros((4, 6, 3), dtype=np.uint8), frame_id="first", frame_index=1)
        loop.wait_for_cycle()
        with patch.object(FrameLoop, "frame_jpeg", side_effect=ValueError("bad image")):
            status, transaction = _json(routes.handle("GET", "/host/observation/transaction"))
        self.assertEqual(status, 503)
        self.assertEqual(transaction["publication"]["health"], "error")
        self.assertIsNone(transaction["image_base64"])

    def test_decision_transaction_image_stays_with_its_completed_cycle(self) -> None:
        from autonomy.decision_cycle.activation import DECISION_STEPS
        from implementations.decision_cycle.catalog import packaged_activation

        activations = {step: packaged_activation(step) for step in DECISION_STEPS}
        perception = GatedPerception(2, blocked=(1,))
        loop, routes = _routes(
            replace(decision_steps(activations), perception=perception),
            decision_activations={
                step: activation.to_payload() for step, activation in activations.items()
            },
        )
        loop.submit(
            np.zeros((4, 6, 3), dtype=np.uint8),
            frame_id="sim_frame_000042",
            frame_index=42,
        )
        loop.wait_for_cycle()
        status, transaction = _json(routes.handle("GET", "/host/decision/transaction"))
        self.assertEqual(status, 200, transaction)
        self.assertEqual(transaction["schema"], "automa_decision_transaction")
        self.assertEqual(transaction["publication"]["decision"]["frame_id"], "sim_frame_000042")
        self.assertEqual(transaction["image_content_type"], "image/jpeg")
        self.assertTrue(base64.b64decode(transaction["image_base64"]).startswith(b"\xff\xd8"))
        try:
            loop.submit(
                np.full((4, 6, 3), 255, dtype=np.uint8),
                frame_id="sim_frame_000043",
                frame_index=43,
            )
            perception.wait_started(1)
            status, transaction = _json(routes.handle("GET", "/host/decision/transaction"))
            self.assertEqual(status, 200, transaction)
            self.assertEqual(transaction["publication"]["decision"]["frame_id"], "sim_frame_000042")
            self.assertEqual(
                _json(routes.handle("GET", "/host/camera/latest"))[1]["frame"]["frame_id"],
                "sim_frame_000043",
            )
        finally:
            perception.release_all()
            loop.wait_for_cycle()

    def test_decision_route_fails_closed_with_a_reason(self) -> None:
        _, routes = _routes()
        status, payload = _json(routes.handle("GET", "/host/decision/latest"))
        self.assertEqual((status, payload["reason"]), (503, "missing"))

    def test_catalog_reads_and_rejects_malformed_writes(self) -> None:
        _, routes = _routes()
        status, payload = _json(routes.handle("GET", "/api/plugins"))
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(_json(routes.handle("POST", "/api/plugins", b"[]",
                         headers={"Content-Type": "application/json"}))[0], 400)

    def test_a_failed_loop_stops_the_session_with_its_error(self) -> None:
        loop, routes = _routes()
        loop.start(loop.host.configuration.__class__(mode="observe_only", num_decisions=None))
        loop.stop(reason="error", error="ChasePassiveCaptureError: session moved")
        _, payload = _json(routes.handle("GET", "/host/status"))
        self.assertEqual(payload["session"]["status"], "error")
        self.assertEqual(payload["host"]["last_error"], "ChasePassiveCaptureError: session moved")


class TelemetryRouteTests(unittest.TestCase):
    def test_missing_publisher_is_unavailable(self) -> None:
        _, routes = _routes()
        status, payload = _json(routes.handle("GET", "/host/telemetry/latest"))
        self.assertEqual((status, payload["reason"], payload["record"]), (503, "publisher_missing", None))

    def test_routes_are_read_only_and_queries_exact(self) -> None:
        calls = []

        class Telemetry:
            def latest(self):
                return {"ok": True, "record": {}}

            def records(self, *, after_sequence, limit):
                calls.append((after_sequence, limit))
                return {"ok": True, "records": []}

        _, routes = _routes(telemetry=Telemetry())
        self.assertEqual(_json(routes.handle("GET", "/host/telemetry/latest"))[0], 200)
        self.assertEqual(_json(routes.handle("GET", "/host/telemetry/latest?x=1"))[1]["reason"], "query_invalid")
        self.assertEqual(_json(routes.handle("POST", "/host/telemetry/latest"))[0], 405)
        self.assertEqual(_json(routes.handle("GET", "/host/telemetry/records?limit=5"))[0], 400)
        self.assertEqual(
            _json(routes.handle("GET", "/host/telemetry/records?after_sequence=1&limit=5&limit=6"))[0], 400,
        )
        self.assertEqual(_json(routes.handle("GET", "/host/telemetry/records?after_sequence=1&limit=5"))[0], 200)
        self.assertEqual(calls, [("1", "5")])


if __name__ == "__main__":
    unittest.main(verbosity=2)
