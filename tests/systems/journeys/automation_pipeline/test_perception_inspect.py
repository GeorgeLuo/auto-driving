from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests.support.cli_runner import run_automa
from tests.support.fake_metrics_ui import fake_metrics_ui_server


class PerceptionInspectBundleTests(unittest.TestCase):
    def test_live_selection_overrides_run_bundle_code_without_restaging(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, fake_metrics_ui_server() as ws_url:
            runtime_root = Path(tmp) / "vehicles"
            env = {"CHASE_UI_WS_URL": ws_url}
            run_automa(
                "vehicles", "update", "perception", "--id", "chase-sim-chaser",
                "--plugin", "frame", runtime_root=runtime_root, extra_env=env,
            )
            bundle = runtime_root / "chase-sim-chaser/bundle"
            activation_path = bundle / "runtime/perception/active.json"
            staged = activation_path.read_bytes()
            # Make the disposable bundle distinguishable from workspace code.
            plugin_path = bundle / "implementations/decision_cycle/perception/plugins/frame/plugin.py"
            plugin_path.write_text(
                plugin_path.read_text(encoding="utf-8").replace(
                    'label="front camera frame"', 'label="staged camera frame"'
                ),
                encoding="utf-8",
            )

            for selection in ((), ("--plugin", "frame"), ("--preset", "sim_debug")):
                with self.subTest(selection=selection):
                    result = run_automa(
                        "vehicles", "perception", "inspect", "--id", "chase-sim-chaser",
                        "--frames", "1", "--interval-s", "0", "--json", *selection,
                        runtime_root=runtime_root, extra_env=env,
                    )
                    report = json.loads(result.stdout)
                    things = report["frames"][0]["perception"]["things"]
                    frame = next(item for item in things if item["thing_id"] == "front_camera_frame")
                    self.assertEqual(frame["label"], "staged camera frame")
                    self.assertFalse(report["perception"]["bundle_refreshed"])
                    self.assertEqual(activation_path.read_bytes(), staged)


if __name__ == "__main__":
    unittest.main(verbosity=2)
