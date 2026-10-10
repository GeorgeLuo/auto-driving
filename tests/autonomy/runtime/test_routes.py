"""The shared runtime HTTP contract, served over the shared frame loop."""
from __future__ import annotations

import json
import threading
import unittest

import numpy as np

from autonomy.decision_cycle.steps import decision_steps
from autonomy.runtime.cycle_host import AutonomyCycleHost
from autonomy.runtime.frame_loop import FrameLoop
from autonomy.runtime.routes import RuntimeRoutes


class _Target:
    def acquire(self):
        return None

    def write(self, control):
        return {"boundary": "test", "transport": "in_process"}

    def release(self):
        pass


def _routes(**kwargs) -> tuple[FrameLoop, RuntimeRoutes]:
    loop = FrameLoop(
        host=AutonomyCycleHost(steps=decision_steps(), target=_Target()),
        interval_s=0.0, vehicle_id="test-car", runtime="test", run_id="test-run",
    )
    return loop, RuntimeRoutes(loop, **kwargs)


def _json(response):
    status, headers, body = response
    return status, json.loads(body)


class RuntimeRoutesTests(unittest.TestCase):
    def test_status_reports_the_execution_mode_and_host_run(self) -> None:
        _, routes = _routes()
        status, payload = _json(routes.handle("GET", "/autonomy/status"))
        self.assertEqual(status, 200)
        self.assertEqual(payload["mode"], "manual")
        self.assertEqual(payload["host_run_id"], "test-run")
        self.assertEqual(payload["session"]["status"], "stopped")
        self.assertNotIn("drive_mode", payload)

    def test_runtime_commands_start_and_stop_the_shared_session(self) -> None:
        _, routes = _routes()
        body = json.dumps({"command": "run", "configuration": {"mode": "observe_only", "num_decisions": 2}})
        status, payload = _json(routes.handle("POST", "/autonomy/runtime", body.encode()))
        self.assertEqual(status, 200)
        self.assertEqual(payload["session"]["status"], "running")
        self.assertEqual(_json(routes.handle("GET", "/autonomy/status"))[1]["mode"], "observe_only")
        status, payload = _json(routes.handle("POST", "/autonomy/runtime", b'{"command": "stop"}'))
        self.assertEqual(payload["session"]["status"], "stopped")

    def test_bad_requests_return_the_reason(self) -> None:
        _, routes = _routes()
        self.assertEqual(
            _json(routes.handle("POST", "/autonomy/runtime", b'{"command": "fly"}')),
            (400, {"ok": False, "error": "unknown runtime command 'fly'"}),
        )
        status, payload = _json(routes.handle("POST", "/autonomy/runtime", b"{"))
        self.assertEqual(status, 400)
        self.assertIn("not JSON", payload["error"])
        self.assertEqual(_json(routes.handle("GET", "/autonomy/nothing"))[0], 404)
        self.assertEqual(_json(routes.handle("POST", "/autonomy/status"))[0], 405)
        self.assertEqual(_json(routes.handle("POST", "/autonomy/mode", b"{}"))[0], 404)

    def test_restart_stops_then_replaces_the_process(self) -> None:
        restarted = threading.Event()
        _, routes = _routes(on_restart=restarted.set)
        status, payload = _json(routes.handle("POST", "/autonomy/runtime", b'{"command": "restart"}'))
        self.assertEqual(status, 200)
        self.assertTrue(restarted.wait(2.0))
        _, fixed = _routes()
        self.assertEqual(_json(fixed.handle("POST", "/autonomy/runtime", b'{"command": "restart"}'))[0], 400)

    def test_submitted_frames_publish_observation_camera_and_jpeg(self) -> None:
        loop, routes = _routes()
        self.assertEqual(_json(routes.handle("GET", "/autonomy/observation/latest"))[1]["health"], "warming")
        self.assertTrue(loop.due())
        loop.submit(np.zeros((4, 6, 3), dtype=np.uint8), frame_id="sim_frame_000042", frame_index=42,
                    metadata={"simulator_frame_index": 42})
        loop.wait_for_cycle()
        status, payload = _json(routes.handle("GET", "/autonomy/observation/latest"))
        self.assertEqual(status, 200)
        self.assertEqual(payload["frame"]["frame_id"], "sim_frame_000042")
        self.assertEqual(payload["mode"], "manual")
        status, headers, body = routes.handle("GET", "/autonomy/observation/latest/frame.jpg")
        self.assertEqual((status, headers["Content-Type"], headers["X-Frame-Id"]), (200, "image/jpeg", "sim_frame_000042"))
        self.assertTrue(body.startswith(b"\xff\xd8"))
        status, headers, _ = routes.handle("HEAD", "/autonomy/camera/latest/frame.jpg")
        self.assertEqual((status, headers["X-Frame-Index"]), (200, "42"))
        self.assertEqual(_json(routes.handle("GET", "/autonomy/camera/latest"))[1]["perception_state"], "matched")

    def test_decision_and_memory_routes_fail_closed_with_a_reason(self) -> None:
        _, routes = _routes()
        status, payload = _json(routes.handle("GET", "/autonomy/decision/latest"))
        self.assertEqual((status, payload["reason"]), (503, "missing"))
        status, payload = _json(routes.handle("POST", "/autonomy/memory/reset", b""))
        self.assertEqual((status, payload["status"]), (503, "absent"))
        self.assertEqual(payload["schema"], "automa_memory_reset_v0")

    def test_catalog_reads_and_rejects_malformed_writes(self) -> None:
        _, routes = _routes()
        status, payload = _json(routes.handle("GET", "/api/plugins"))
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(_json(routes.handle("POST", "/api/plugins", b"[]"))[0], 400)

    def test_a_failed_loop_stops_the_session_with_its_error(self) -> None:
        loop, routes = _routes()
        loop.start(loop.host.configuration.__class__(mode="observe_only"))
        loop.stop(reason="error", error="ChasePassiveCaptureError: session moved")
        _, payload = _json(routes.handle("GET", "/autonomy/status"))
        self.assertEqual(payload["session"]["status"], "error")
        self.assertEqual(payload["autonomy"]["last_error"], "ChasePassiveCaptureError: session moved")


class TelemetryRouteTests(unittest.TestCase):
    def test_missing_publisher_is_unavailable(self) -> None:
        _, routes = _routes()
        status, payload = _json(routes.handle("GET", "/autonomy/telemetry/latest"))
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
        self.assertEqual(_json(routes.handle("GET", "/autonomy/telemetry/latest"))[0], 200)
        self.assertEqual(_json(routes.handle("GET", "/autonomy/telemetry/latest?x=1"))[1]["reason"], "query_invalid")
        self.assertEqual(_json(routes.handle("POST", "/autonomy/telemetry/latest"))[0], 405)
        self.assertEqual(_json(routes.handle("GET", "/autonomy/telemetry/records?limit=5"))[0], 400)
        self.assertEqual(
            _json(routes.handle("GET", "/autonomy/telemetry/records?after_sequence=1&limit=5&limit=6"))[0], 400,
        )
        self.assertEqual(_json(routes.handle("GET", "/autonomy/telemetry/records?after_sequence=1&limit=5"))[0], 200)
        self.assertEqual(calls, [("1", "5")])


if __name__ == "__main__":
    unittest.main(verbosity=2)
