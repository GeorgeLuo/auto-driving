from __future__ import annotations

import io
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from cli.automa_cli.automation import run_vehicle_automation
from cli.automa_cli.bundles import controller_bundle_paths
from cli.automa_cli.runtime_hosts import stop_chase_host
from tests.integration.automation_pipeline.cadence_fixtures import GatedPerception
from tests.integration.automation_pipeline.pipeline_fixtures import (
    VEHICLE_ID, _FakeCar, _write_activations, chase_runtime, staged_runners,
)
from tests.support.cli_runner import run_automa
from tests.support.fake_metrics_ui import fake_metrics_ui_server


class ChaseCaptureCadenceTests(unittest.TestCase):
    def _run(self, runtime_root: Path, car: _FakeCar, perception: GatedPerception,
             *, interval_s: float, num_decisions: int):
        bundle = controller_bundle_paths(runtime_root / VEHICLE_ID)
        _write_activations(bundle)
        output = io.StringIO()
        with staged_runners(perception=perception), chase_runtime(runtime_root, car=car):
            result = run_vehicle_automation(
                vehicle_id=VEHICLE_ID, interval_s=interval_s, num_decisions=num_decisions,
                take_control=False, record=True, verbose=True, output=output,
            )
        state = json.loads((Path(bundle["runtime_dir"]) / "automation" / "state.json").read_text())
        return result, state, output.getvalue()

    def test_latest_capture_drains_during_another_capture_and_reports_exact_skips(self) -> None:
        perception = GatedPerception(2)

        class CaptureCar(_FakeCar):
            def read_sensors(self, request):
                if self.capture_count == 1:
                    perception.wait_started(0)
                if self.capture_count == 3:
                    # Captures 1 and 2 are pending while perception handles 0.
                    # Hold capture 3 in flight as the worker takes capture 2.
                    perception.release[0].set()
                    perception.wait_started(1)
                return super().read_sensors(request)

        with tempfile.TemporaryDirectory() as tmp:
            try:
                result, state, _output = self._run(
                    Path(tmp), CaptureCar(simulator_frame_stride=7), perception,
                    interval_s=0.0, num_decisions=2,
                )
            finally:
                perception.release_all()
            self.assertEqual(result.exit_code, 0, result.message)
            contexts = perception.contexts
            self.assertEqual([c.metadata["capture_sequence"] for c in contexts], [0, 2])
            self.assertEqual([c.frame_index for c in contexts], [100, 114])
            self.assertEqual([c.metadata["skipped_since_previous"] for c in contexts], [0, 1])
            self.assertEqual(state["skipped_count"], 1)
            self.assertEqual(state["num_decisions"], state["processed_count"])
            self.assertEqual(state["session"]["processed_decisions"], 2)
            self.assertGreater(state["frames_captured"], state["processed_count"] + state["skipped_count"])
            records = [json.loads(p.read_text()) for p in Path(state["run_dir"]).glob("perception/*/perception.json")]
            records.sort(key=lambda r: r["context"]["metadata"]["capture_sequence"])
            self.assertEqual([r["skipped_since_previous"] for r in records], [0, 1])
            self.assertEqual([r["context"]["metadata"]["simulator_frame_index"] for r in records], [100, 114])
            self.assertEqual(records[-1]["context"]["metadata"]["simulation_epoch"], "chase-run:test")
            self.assertIn("Decisions completed: 2", result.message)
            self.assertIn("Frames superseded before decision: 1", result.message)
            self.assertIn("Decisions recorded: 2", result.message)

    def test_bounded_completion_wakes_a_long_capture_interval(self) -> None:
        perception = GatedPerception(1)
        completed = threading.Event()
        results = []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def run() -> None:
                try:
                    results.append(self._run(root, _FakeCar(), perception, interval_s=10.0, num_decisions=1))
                finally:
                    completed.set()

            thread = threading.Thread(target=run)
            thread.start()
            try:
                perception.wait_started(0)
                state_path = root / "chase-sim-chaser/bundle/runtime/automation/state.json"
                deadline = time.monotonic() + 2.0
                while json.loads(state_path.read_text())["frames_captured"] != 1:
                    self.assertLess(time.monotonic(), deadline, "capture was not published")
                    time.sleep(0.01)
                perception.release[0].set()
                self.assertTrue(completed.wait(timeout=2.0), "completion waited for the capture interval")
            finally:
                perception.release_all()
                thread.join(timeout=12.0)
            result, state, _output = results[0]
            self.assertEqual(result.exit_code, 0, result.message)
            self.assertEqual((state["status"], state["frames_captured"], state["processed_count"]), ("completed", 1, 1))

    def test_cli_num_decisions_limits_the_completed_shared_cycles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, fake_metrics_ui_server() as ws_url:
            root = Path(tmp)
            env = {"CHASE_UI_WS_URL": ws_url}
            # Staging builds the controller release the car's runtime host runs.
            run_automa(
                "vehicles", "update", "perception", "--id", "chase-sim-chaser",
                "--plugin", "frame", runtime_root=root, extra_env=env,
            )
            try:
                run = run_automa(
                    "vehicles", "automation", "run", "--id", "chase-sim-chaser",
                    "--observe-only", "--num-decisions", "3", "--interval-s", "0",
                    "--foreground", runtime_root=root, extra_env=env,
                )
            finally:
                with patch("cli.automa_cli.runtime_hosts.RUNTIME_ROOT", root):
                    stop_chase_host("chase-sim-chaser")
            state = json.loads((root / "chase-sim-chaser/bundle/runtime/automation/state.json").read_text())
            self.assertEqual(state["status"], "completed")
            self.assertEqual(state["num_decisions"], 3)
            self.assertEqual(state["processed_count"], 3)
            self.assertEqual(state["session"]["configuration"]["num_decisions"], 3)
            self.assertEqual(state["session"]["processed_decisions"], 3)
            self.assertIn("Decisions: 3", run.stdout)
            self.assertIn("Decisions completed: 3", run.stdout)
