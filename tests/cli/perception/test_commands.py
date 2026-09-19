from __future__ import annotations
import json
import os
import tempfile
import unittest
from pathlib import Path
from PIL import Image
from cli.automa_cli.runtime_view import RuntimeViewServer
from tests.support.cli_runner import run_automa
from tests.support.runtime_fixtures import write_json, write_runtime_fixture


ROOT = Path(__file__).resolve().parents[3]


class PerceptionCommandTests(unittest.TestCase):
    def test_perception_replay_is_not_retained_as_an_alias(self) -> None:
        result = run_automa(
            "vehicles",
            "perception",
            "replay",
            check=False,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid choice: 'replay'", result.stderr)

    def test_perception_apply_is_offline_and_does_not_record_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            frames.mkdir()
            from PIL import Image

            Image.new("RGB", (32, 24), (30, 40, 50)).save(frames / "000.png")
            Image.new("RGB", (32, 24), (50, 40, 30)).save(frames / "001.png")
            apply_root = root / "applies"
            result = run_automa(
                "vehicles",
                "perception",
                "apply",
                str(frames),
                "--json",
                extra_env={"AUTOMA_PERCEPTION_APPLY_ROOT": str(apply_root)},
            )

            payload = json.loads(result.stdout)
            self.assertEqual(payload["schema"], "perception_experiment_v0")
            self.assertEqual(payload["source"]["kind"], "apply")
            self.assertEqual(payload["summary"]["frames"], 2)
            self.assertFalse(payload["recording"])
            self.assertFalse(apply_root.exists())

    def test_scenario_deployed_perception_schema_is_machine_readable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            write_runtime_fixture(
                runtime_root,
                "chase-sim-chaser",
                pid=os.getpid(),
                manifest_bundle_root=ROOT,
            )

            result = run_automa(
                "vehicles",
                "info",
                "perception",
                "--id",
                "chase-sim-chaser",
                "--json",
                runtime_root=runtime_root,
            )

        payload = json.loads(result.stdout)
        self.assertEqual(payload["schema"], "vehicle_perception_info_v0")
        self.assertEqual(payload["activation"]["algorithm"], "sim_debug")
        self.assertEqual(
            payload["algorithm_schema"]["schema"], "perception_algorithm_schema_v2"
        )
        self.assertEqual(
            payload["algorithm_schema"]["output"]["schema"], "perception_text_v2"
        )
        self.assertFalse(payload["published_view"]["available"])

    def test_perception_info_reports_running_view_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            vehicle_id = "chase-sim-chaser"
            write_runtime_fixture(
                runtime_root,
                vehicle_id,
                pid=os.getpid(),
                manifest_bundle_root=ROOT,
            )
            automation_dir = (
                runtime_root / vehicle_id / "bundle" / "runtime" / "automation"
            )
            server = RuntimeViewServer(
                vehicle_id=vehicle_id,
                automation_dir=automation_dir,
                port=0,
                run_id="test-run",
                worker_pid=os.getpid(),
            ).start()
            frame_path = Path(tmp) / "current-frame.png"
            Image.new("RGB", (16, 12), (20, 40, 60)).save(frame_path)
            frame_record = {
                "frame_id": "frame_000002",
                "frame_index": 2,
                "captured_at_ms": 1000,
                "perception": {"things": [], "signals": []},
                "sensor_snapshot": {
                    "readings": {
                        "front_camera": {
                            "metadata": {"content_type": "image/png"},
                        }
                    }
                },
            }
            server.perception.publish_frame(
                frame_path=frame_path, frame_record=frame_record
            )
            server.perception.publish_perception(frame_record=frame_record)
            expected_url = server.url
            try:
                text_result = run_automa(
                    "vehicles",
                    "info",
                    "perception",
                    "--id",
                    vehicle_id,
                    runtime_root=runtime_root,
                )
                json_result = run_automa(
                    "vehicles",
                    "info",
                    "perception",
                    "--id",
                    vehicle_id,
                    "--json",
                    runtime_root=runtime_root,
                )
            finally:
                server.stop()

        payload = json.loads(json_result.stdout)
        self.assertTrue(payload["published_view"]["available"])
        self.assertEqual(payload["published_view"]["url"], expected_url)
        self.assertIn("Perception view: http://127.0.0.1:", text_result.stdout)

    def test_perception_info_reports_worker_that_exited_during_startup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            vehicle_id = "chase-sim-chaser"
            dead_pid = 987654321
            write_runtime_fixture(
                runtime_root,
                vehicle_id,
                pid=dead_pid,
                manifest_bundle_root=ROOT,
            )
            state_path = (
                runtime_root
                / vehicle_id
                / "bundle"
                / "runtime"
                / "automation"
                / "state.json"
            )
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state.update({"status": "starting", "pid": dead_pid})
            write_json(state_path, state)

            result = run_automa(
                "vehicles",
                "info",
                "perception",
                "--id",
                vehicle_id,
                runtime_root=runtime_root,
            )

        self.assertIn("Perception view: unavailable", result.stdout)
        self.assertIn("exited during startup", result.stdout)
        self.assertNotIn("Connection refused", result.stdout)

    def test_perception_info_reports_live_worker_that_is_still_starting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            vehicle_id = "chase-sim-chaser"
            write_runtime_fixture(
                runtime_root,
                vehicle_id,
                pid=os.getpid(),
                manifest_bundle_root=ROOT,
            )
            state_path = (
                runtime_root
                / vehicle_id
                / "bundle"
                / "runtime"
                / "automation"
                / "state.json"
            )
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state.update({"status": "starting", "pid": os.getpid()})
            write_json(state_path, state)

            result = run_automa(
                "vehicles",
                "info",
                "perception",
                "--id",
                vehicle_id,
                runtime_root=runtime_root,
            )

        self.assertIn("Perception view: starting", result.stdout)
        self.assertIn("still initializing", result.stdout)
        self.assertNotIn("start or restart the automation worker", result.stdout)
