from __future__ import annotations

import io
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from cli.automa_cli.automation import run_vehicle_automation
from cli.automa_cli.bundles import controller_bundle_paths
from tests.deterministic.cli.automation_pipeline.cadence_fixtures import GatedPerception
from tests.shared.cli.automation_pipeline.pipeline_fixtures import (
    VEHICLE_ID, _FakeCar, _write_activations, chase_runtime, staged_runners,
)


class ChaseCaptureCadenceTests(unittest.TestCase):
    def _run(self, runtime_root: Path, car: _FakeCar, perception: GatedPerception,
             *, num_decisions: int):
        bundle = controller_bundle_paths(runtime_root / VEHICLE_ID)
        _write_activations(bundle)
        output = io.StringIO()
        with staged_runners(perception=perception), chase_runtime(runtime_root, car=car):
            result = run_vehicle_automation(
                vehicle_id=VEHICLE_ID, num_decisions=num_decisions,
                take_control=False, record=True, verbose=True, output=output,
            )
        state = json.loads((Path(bundle["runtime_dir"]) / "automation" / "state.json").read_text())
        return result, state, output.getvalue()

    def test_next_capture_starts_when_the_worker_releases_the_frame(self) -> None:
        perception = GatedPerception(2)
        completed = threading.Event()
        results = []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def run() -> None:
                try:
                    results.append(self._run(root, _FakeCar(), perception, num_decisions=2))
                finally:
                    completed.set()

            thread = threading.Thread(target=run)
            thread.start()
            try:
                perception.wait_started(0)
                state_path = root / "chase-sim-chaser/bundle/runtime/automation/state.json"
                deadline = time.monotonic() + 2.0
                while True:
                    try:
                        state = json.loads(state_path.read_text())
                    except FileNotFoundError:
                        state = {}
                    if state.get("frames_received") == 1:
                        break
                    self.assertLess(time.monotonic(), deadline, "capture was not published")
                    time.sleep(0.01)
                time.sleep(0.05)
                held = json.loads(state_path.read_text())
                self.assertEqual(held["frames_received"], 1)
                self.assertEqual(perception.contexts[0].metadata["capture_sequence"], 0)
                perception.release[0].set()
                self.assertTrue(completed.wait(timeout=2.0), "completion waited after the worker finished")
            finally:
                perception.release_all()
                thread.join(timeout=5.0)
            result, state, _output = results[0]
            self.assertEqual(result.exit_code, 0, result.message)
            contexts = perception.contexts
            self.assertEqual([c.metadata["capture_sequence"] for c in contexts], [0, 1])
            self.assertEqual([c.frame_index for c in contexts], [100, 101])
            self.assertEqual((state["frames_received"], state["processed_count"]), (2, 2))
            self.assertEqual(state["num_decisions"], state["processed_count"])
            self.assertEqual(state["session"]["processed_decisions"], 2)
            records = [json.loads(p.read_text()) for p in Path(state["run_dir"]).glob("perception/*/perception.json")]
            records.sort(key=lambda r: r["context"]["metadata"]["capture_sequence"])
            self.assertEqual([r["context"]["metadata"]["simulator_frame_index"] for r in records], [100, 101])
            self.assertEqual(records[-1]["context"]["metadata"]["simulation_epoch"], "chase-run:test")
            self.assertIn("Frames received: 2", result.message)
            self.assertIn("Decisions completed: 2", result.message)
            self.assertIn("Decisions recorded: 2", result.message)

    def test_bounded_completion_does_not_wait_after_the_worker_finishes(self) -> None:
        perception = GatedPerception(1)
        completed = threading.Event()
        results = []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def run() -> None:
                try:
                    results.append(self._run(root, _FakeCar(), perception, num_decisions=1))
                finally:
                    completed.set()

            thread = threading.Thread(target=run)
            thread.start()
            try:
                perception.wait_started(0)
                state_path = root / "chase-sim-chaser/bundle/runtime/automation/state.json"
                deadline = time.monotonic() + 2.0
                while True:
                    # Perception can start before the CLI publishes its first state.
                    try:
                        state = json.loads(state_path.read_text())
                    except FileNotFoundError:
                        state = {}
                    if state.get("frames_received") == 1:
                        break
                    self.assertLess(time.monotonic(), deadline, "capture was not published")
                    time.sleep(0.01)
                perception.release[0].set()
                self.assertTrue(completed.wait(timeout=2.0), "completion waited after the worker finished")
            finally:
                perception.release_all()
                thread.join(timeout=5.0)
            result, state, _output = results[0]
            self.assertEqual(result.exit_code, 0, result.message)
            self.assertEqual((state["status"], state["frames_received"], state["processed_count"]), ("completed", 1, 1))
