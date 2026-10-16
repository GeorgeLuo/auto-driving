"""The Chase adapter: simulator captures into the shared loop behind the shared routes."""
from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

import numpy as np

from autonomy.runtime.client import RuntimeClient
from autonomy.runtime.session import RunConfiguration
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorFrame, SensorReading
from cli.automa_cli.bundles import controller_bundle_paths
from implementations.runtime.chase_sim.service import ChaseRuntimeHost
from tests.shared.cli.automation_pipeline.pipeline_fixtures import (
    _FakeCar, _SlowMapper, _write_activations, staged_runners,
)
from tests.shared.implementations.runtime.wait import wait_until as _wait


class ChaseRuntimeHostTests(unittest.TestCase):
    def test_stream_captures_during_a_slow_cycle_and_decides_only_newest_pending_frame(self) -> None:
        release_first_cycle = threading.Event()

        class Stream:
            count = 0
            closed = False

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                self.closed = True

            def read_camera_frame(self):
                time.sleep(0.005)
                self.count += 1
                if self.count == 5:
                    release_first_cycle.set()
                now = int(time.time() * 1000)
                return SensorFrame(
                    read_id=f"stream-{self.count}", started_at_ms=now, completed_at_ms=now,
                    readings={FRONT_CAMERA_SENSOR_ID: SensorReading(
                        sensor_id=FRONT_CAMERA_SENSOR_ID, sensor_kind="camera", captured_at_ms=now,
                        value=np.full((4, 4, 3), self.count % 256, dtype=np.uint8),
                    )},
                    metadata={"simulator_frame_index": self.count, "simulation_epoch": "fixture"},
                )

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
                car=car, runtime_dir=Path(bundle["runtime_dir"]), vehicle_id="chase-sim-chaser",
            ) as host:
                client = RuntimeClient(host.base_url)
                client.start(RunConfiguration(mode="observe_only", num_decisions=2))
                session = _wait(lambda: (s := client.status()["session"])["status"] == "completed" and s,
                                message="stream session did not complete")
                _wait(lambda: stream.closed, message="terminal run did not unsubscribe")
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
                                  vehicle_id="chase-sim-chaser") as host:
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
                observation = client.host_status()["autonomy"]["components"]["observation"]
                decision = client.request("GET", "/autonomy/decision/latest")

        self.assertEqual((session["status"], session["processed_decisions"]), ("completed", 2))
        self.assertLessEqual(car.capture_count, captured + 1, "the host captured after its session ended")
        self.assertEqual(observation["latest"]["mode"], "observe_only")
        self.assertFalse(observation["latest"]["control"]["applied"])
        self.assertEqual(decision["status"], "ready", decision)


if __name__ == "__main__":
    unittest.main()
