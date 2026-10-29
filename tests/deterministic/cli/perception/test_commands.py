from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from cli.automa_cli.frontend.server import RuntimeViewServer
from tests.shared.cli.invoke import invoke_automa
from tests.support.cli_runner import WORKSPACE_ROOT as ROOT
from tests.support.runtime_fixtures import write_runtime_fixture


class PerceptionCommandTests(unittest.TestCase):
    def test_scenario_deployed_perception_schema_is_machine_readable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            write_runtime_fixture(
                runtime_root,
                "chase-sim-chaser",
                pid=os.getpid(),
                manifest_bundle_root=ROOT,
            )

            result = invoke_automa(
                "hosts", "info",
                "perception",
                "--name",
                "chase-sim-chaser",
                "--json",
                runtime_root=runtime_root,
            )

        payload = json.loads(result.stdout)
        self.assertEqual(payload["schema"], "host_perception_info")
        self.assertEqual(payload["activation"]["preset"], "sim_debug")
        self.assertEqual(
            payload["perception_schema"]["schema"], "perception_schema_v3"
        )
        self.assertEqual(
            payload["perception_schema"]["output"]["schema"], "perception_text_v2"
        )
        self.assertFalse(payload["published_view"]["available"])

    def test_perception_info_reports_running_view_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            host_name = "chase-sim-chaser"
            write_runtime_fixture(
                runtime_root,
                host_name,
                pid=os.getpid(),
                manifest_bundle_root=ROOT,
            )
            automation_dir = (
                runtime_root / host_name / "bundle" / "runtime" / "automation"
            )
            server = RuntimeViewServer(
                host_name=host_name,
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
                "sensor_frame": {
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
                text_result = invoke_automa(
                    "hosts", "info",
                    "perception",
                    "--name",
                    host_name,
                    runtime_root=runtime_root,
                )
                json_result = invoke_automa(
                    "hosts", "info",
                    "perception",
                    "--name",
                    host_name,
                    "--json",
                    runtime_root=runtime_root,
                )
            finally:
                server.stop()

        payload = json.loads(json_result.stdout)
        self.assertTrue(payload["published_view"]["available"])
        self.assertEqual(payload["published_view"]["url"], expected_url)
        self.assertIn("Perception view: http://127.0.0.1:", text_result.stdout)


if __name__ == "__main__":
    unittest.main()
