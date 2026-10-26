"""The Chase adapter: simulator captures into the shared loop behind the shared routes."""
from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from autonomy.runtime.client import RuntimeClient
from autonomy.runtime.frame_loop import encode_jpeg
from autonomy.runtime.session import RunConfiguration
from implementations.vehicle.chase_sim import ChaseCameraCapture
from cli.automa_cli.bundles import controller_bundle_paths
from implementations.runtime.chase_sim.service import ChaseRuntimeHost
from tests.shared.cli.automation_pipeline.pipeline_fixtures import (
    _FakeCar, _SlowMapper, _write_activations, staged_runners,
)
from tests.shared.implementations.runtime.wait import wait_until as _wait


class ChaseRuntimeHostTests(unittest.TestCase):
    def test_failed_stream_startup_never_submits_the_subscription_frame(self):
        from implementations.vehicle.chase_sim.camera_stream import ChaseCameraStream
        from implementations.vehicle.chase_sim.metrics_ws import MetricsUiWsClient
        from tests.deterministic.implementations.vehicle.chase_frame_identity_fixtures import _Connection

        connection = _Connection()
        connection.state.return_value = {}
        car = _FakeCar()
        car.camera_stream = lambda: ChaseCameraStream(MetricsUiWsClient("ws://fixture.invalid/ws"))
        with tempfile.TemporaryDirectory() as tmp, patch(
            "implementations.vehicle.chase_sim.camera_stream.MetricsUiConnection", return_value=connection,
        ):
            bundle = controller_bundle_paths(Path(tmp) / "chase-sim-chaser")
            _write_activations(bundle)
            with ChaseRuntimeHost(car=car, runtime_dir=Path(bundle["runtime_dir"]), host_id="chase-sim-chaser") as host:
                host.loop.start(RunConfiguration(mode="observe_only"))
                _wait(lambda: host.loop.host.run_state == "error", message="startup failure was hidden")
                self.assertIn("simulator_capability_missing", host.loop.host.last_error)
                self.assertNotIn("Camera stream is not ready", host.loop.host.last_error)
                self.assertEqual(host.loop.frames_received, 0)
                self.assertIsNone(host.loop.host.last_context)

    def test_stream_captures_during_a_slow_cycle_and_decides_only_newest_pending_frame(self) -> None:
        release_first_cycle = threading.Event()

        class Stream:
            count = 0
            closed = False

            def __enter__(self):
                return self

            def close(self):
                self.closed = True

            def __exit__(self, *_exc):
                self.close()

            def set_consumer(self, consumer):
                self.consumer = consumer

            def check_progress(self):
                time.sleep(0.005)
                self.count += 1
                if self.count == 5:
                    release_first_cycle.set()
                now = int(time.time() * 1000)
                self.consumer(ChaseCameraCapture(
                    f"chase_frame_{self.count:06d}", self.count, "fixture", now,
                    lambda count=self.count: np.full((4, 4, 3), count % 256, dtype=np.uint8),
                    encode_jpeg(np.zeros((4,4,3),dtype=np.uint8)),
                ))

        stream = Stream()
        car = _FakeCar()
        car.camera_stream = lambda: stream
        class Mapper(_SlowMapper):
            def perceive(self, request):
                if not self.frame_ids and not release_first_cycle.wait(timeout=2.0):
                    raise AssertionError("capture waited for the blocked decision")
                return super().perceive(request)

        mapper = Mapper()
        with tempfile.TemporaryDirectory() as tmp:
            bundle = controller_bundle_paths(Path(tmp) / "chase-sim-chaser")
            _write_activations(bundle)
            with staged_runners(perception=mapper), ChaseRuntimeHost(
                car=car, runtime_dir=Path(bundle["runtime_dir"]), host_id="chase-sim-chaser",
            ) as host:
                client = RuntimeClient(host.base_url)
                client.start(RunConfiguration(mode="observe_only", num_decisions=2))
                session = _wait(lambda: (s := client.status()["session"])["status"] == "completed" and s,
                                message="stream session did not complete")
                _wait(lambda: stream.closed, message="terminal run did not unsubscribe")
                context_metadata = host.loop.latest_state.context["metadata"]
                self.assertNotIn("frames_received", context_metadata)
                self.assertNotIn("capture_duration_ms", context_metadata)
                self.assertNotIn("source_timestamp_us", context_metadata)
        self.assertEqual(session["processed_decisions"], 2)
        self.assertGreater(stream.count, 2)
        self.assertEqual(car.capture_count, 0, "stream path used file-based capture")
        self.assertEqual(len(mapper.frame_ids), 2)
        first, second = (int(frame.rsplit("_", 1)[1]) for frame in mapper.frame_ids)
        self.assertGreater(second, first + 1, "the next cycle processed a queued old frame")

    def test_a_bounded_session_captures_only_until_its_last_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = controller_bundle_paths(Path(tmp) / "chase-sim-chaser")
            _write_activations(bundle)
            car = _FakeCar()
            with ChaseRuntimeHost(car=car, runtime_dir=Path(bundle["runtime_dir"]),
                                  host_id="chase-sim-chaser") as host:
                client = RuntimeClient(host.base_url, timeout_s=2.0)
                client.start(RunConfiguration(mode="observe_only", num_decisions=2))
                session = _wait(
                    lambda: (s := client.status()["session"])["status"] != "running" and s,
                    message="the session did not complete",
                )
                # A read already inside the car can finish after the session
                # leaves running. Allow that one read, then hold.
                captured = car.capture_count
                time.sleep(0.3)
                observation = client.host_status()["host"]["components"]["observation"]
                decision = client.request("GET", "/host/decision/latest")

        self.assertEqual((session["status"], session["processed_decisions"]), ("completed", 2))
        self.assertLessEqual(car.capture_count, captured + 1, "the host captured after its session ended")
        self.assertEqual(observation["latest"]["mode"], "observe_only")
        self.assertFalse(observation["latest"]["control"]["applied"])
        self.assertEqual(decision["status"], "ready", decision)

    def test_restart_during_recording_does_not_invert_host_and_frame_locks(self):
        from autonomy.decision_cycle.steps import decision_steps
        from implementations.runtime.chase_sim import create_host
        from implementations.runtime.chase_sim.service import ChaseFrameLoop
        entered, release, start_locked = threading.Event(), threading.Event(), threading.Event()
        car = _FakeCar()
        with tempfile.TemporaryDirectory() as tmp:
            loop = ChaseFrameLoop(car=car, host=create_host(car, steps=decision_steps()), recording_root=Path(tmp), host_id="fixture")
            self.addCleanup(loop.shutdown)
            loop.start(RunConfiguration(mode="observe_only"), record=True)
            encoder = loop.host.recording.encode_image
            def encode(image):
                entered.set()
                if not release.wait(2):
                    raise AssertionError("recording was not released")
                return encoder(image)
            loop.host.recording.encode_image = encode
            loop.submit(np.zeros((4, 4, 3), dtype=np.uint8))
            self.assertTrue(entered.wait(1))
            errors = []
            def restart():
                try:
                    start_locked.set()
                    loop.start(RunConfiguration(mode="observe_only"))
                except Exception as exc:
                    errors.append(exc)
            worker = threading.Thread(target=restart, daemon=True)
            worker.start()
            self.assertTrue(start_locked.wait(1))
            release.set()
            worker.join(2)
            self.assertFalse(worker.is_alive(), "restart deadlocked with recording JPEG callback")
            loop.wait_for_cycle()
            self.assertEqual(errors, [])

    def test_restart_closes_previous_stream_and_preserves_process_counters(self):
        streams = []
        class Stream:
            def __init__(self):
                self.closed = threading.Event()
            def set_consumer(self, consumer):
                self.consumer = consumer
                self.consumer(self.first)
            def __enter__(self):
                index = len(streams)
                self.first = ChaseCameraCapture(
                    f"chase_frame_{index:06d}", index, "fixture", int(time.time() * 1000),
                    lambda: np.zeros((4, 4, 3), dtype=np.uint8), encode_jpeg(np.zeros((4,4,3),dtype=np.uint8)),
                )
                return self
            def close(self):
                self.closed.set()
            def __exit__(self, *_exc):
                self.close()
            def check_progress(self):
                pass
        car = _FakeCar()
        def open_stream():
            stream = Stream()
            streams.append(stream)
            return stream
        car.camera_stream = open_stream
        with tempfile.TemporaryDirectory() as tmp:
            bundle = controller_bundle_paths(Path(tmp) / "chase-sim-chaser")
            _write_activations(bundle)
            with ChaseRuntimeHost(car=car, runtime_dir=Path(bundle["runtime_dir"]),
                                  host_id="chase-sim-chaser") as host:
                host.loop.start(RunConfiguration(mode="observe_only"))
                _wait(lambda: len(streams) == 1, message="first stream did not open")
                host.loop.start(RunConfiguration(mode="observe_only"))
                _wait(lambda: len(streams) == 2, message="restart stream did not open")
                self.assertTrue(streams[0].closed.is_set())
                _wait(lambda: host.loop.frames_received == 2, message="second capture was not offered")
                status = host.loop.observation_status()
                self.assertEqual((status["frames_received"], status["frames_dropped"]), (2, 0))
                host.loop.stop()
                self.assertTrue(streams[1].closed.is_set())
                self.assertEqual(host.loop.host.run_state, "stopped")
                self.assertEqual(host.loop.observation_status()["frames_received"], 2)

    def test_unadvertised_stream_ends_run_without_receiving_a_capture(self):
        from implementations.vehicle.chase_sim import ChaseSimCar
        from implementations.vehicle.chase_sim.metrics_ws import MetricsUiCommandResponse
        car = ChaseSimCar(ws_url="ws://fixture.invalid/ws")
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            car.client, "command", return_value=MetricsUiCommandResponse({"payload": {}}, 0.),
        ):
            bundle = controller_bundle_paths(Path(tmp) / "chase-sim-chaser")
            _write_activations(bundle)
            with ChaseRuntimeHost(car=car, runtime_dir=Path(bundle["runtime_dir"]), host_id="chase-sim-chaser") as host:
                host.loop.start(RunConfiguration(mode="observe_only"))
                _wait(lambda: host.loop.host.run_state == "error", message="missing stream accepted")
                self.assertIn("simulator_capability_missing", host.loop.host.last_error)
                self.assertEqual(host.loop.frames_received, 0)

    def test_capability_error_ends_the_run(self):
        from implementations.vehicle.chase_sim.metrics_ws import MetricsUiWebSocketError
        car = _FakeCar()
        def timeout():
            raise MetricsUiWebSocketError("capability timed out")
        car.camera_stream = timeout
        with tempfile.TemporaryDirectory() as tmp:
            bundle = controller_bundle_paths(Path(tmp) / "chase-sim-chaser")
            _write_activations(bundle)
            with ChaseRuntimeHost(car=car, runtime_dir=Path(bundle["runtime_dir"]),
                                  host_id="chase-sim-chaser") as host:
                host.loop.start(RunConfiguration(mode="observe_only"))
                _wait(lambda: host.loop.host.run_state == "error", message="timeout was hidden")
                self.assertIn("capability timed out", host.loop.host.last_error)
                self.assertEqual(car.capture_count, 0)

    def test_the_capture_loop_outlives_a_stop_that_fails_after_the_stream_ends(self):
        # The simulator's frontend drops: its stream ends and the switch to manual fails.
        from implementations.vehicle.chase_sim.metrics_ws import MetricsUiWebSocketError
        car, opened, dropped = _FakeCar(), [], threading.Event()
        def ended():
            opened.append(True)
            dropped.set()
            raise RuntimeError("Camera stream ended")
        car.camera_stream = ended
        with tempfile.TemporaryDirectory() as tmp:
            bundle = controller_bundle_paths(Path(tmp) / "chase-sim-chaser")
            _write_activations(bundle)
            with ChaseRuntimeHost(car=car, runtime_dir=Path(bundle["runtime_dir"]),
                                  host_id="chase-sim-chaser") as host:
                cycle_host = host.loop.host
                set_mode = cycle_host.set_mode
                def disconnected(mode):
                    if mode == "manual" and dropped.is_set():
                        raise MetricsUiWebSocketError("Frontend not connected")
                    return set_mode(mode)
                with patch.object(cycle_host, "set_mode", side_effect=disconnected):
                    host.loop.start(RunConfiguration(mode="observe_only"))
                    _wait(lambda: cycle_host.run_state == "error", message="the ended stream was hidden")
                self.assertEqual(cycle_host.last_error, "RuntimeError: Camera stream ended; "
                                                        "stop failed: MetricsUiWebSocketError: Frontend not connected")
                host.loop.start(RunConfiguration(mode="observe_only"))
                _wait(lambda: len(opened) == 2, message="the next run opened no stream")


if __name__ == "__main__":
    unittest.main()
