"""Replay runs the controller release a recording names, not today's code.

The Chase host runs from a release whose frame plugin differs from this
checkout, as one built from another commit would. After a restage from the
checkout, replaying the recording must still show that release's behaviour.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cli.automa_cli import bundles
from tests.support.cli_runner import WORKSPACE_ROOT, run_automa
from tests.support.fake_metrics_ui import fake_metrics_ui_server

VEHICLE_ID = "chase-sim-chaser"
FRAME_PLUGIN = "implementations/decision_cycle/perception/plugins/frame/plugin.py"


def _package_release_with_frame_label(runtime_root: Path, source_root: Path, label: str) -> dict:
    for package in ("autonomy", "implementations"):
        shutil.copytree(
            WORKSPACE_ROOT / package, source_root / package,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"),
        )
    plugin = source_root / FRAME_PLUGIN
    plugin.write_text(
        plugin.read_text(encoding="utf-8").replace('label="front camera frame"', f'label="{label}"'),
        encoding="utf-8",
    )
    with mock.patch.multiple(
        bundles, ROOT=source_root,
        AUTONOMY_DIR=source_root / "autonomy", IMPLEMENTATIONS_DIR=source_root / "implementations",
    ):
        return bundles.package_controller_bundle(bundles.controller_bundle_paths(runtime_root / VEHICLE_ID))


def _frame_label(report: dict) -> str:
    things = report["frames"][0]["perception"]["things"]
    return next(item for item in things if item["thing_id"] == "front_camera_frame")["label"]


class ReplayByReleaseTests(unittest.TestCase):
    def test_a_recording_replays_on_its_release_after_a_restage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, fake_metrics_ui_server() as ws_url:
            runtime_root = Path(tmp) / "vehicles"
            env = {"CHASE_UI_WS_URL": ws_url}

            def automa(*args: str, check: bool = True):
                return run_automa("vehicles", *args, runtime_root=runtime_root, extra_env=env, check=check)

            automa("update", "perception", "--id", VEHICLE_ID, "--plugin", "frame")
            release = _package_release_with_frame_label(runtime_root, Path(tmp) / "source", "release camera frame")
            try:
                automa(
                    "automation", "run", "--id", VEHICLE_ID, "--observe-only",
                    "--num-decisions", "2", "--interval-s", "0", "--record",
                )
            finally:
                automa("automation", "stop", "--id", VEHICLE_ID, check=False)
            runs = runtime_root / VEHICLE_ID / "bundle/runtime/automation/runs"
            (recording,) = [path for path in runs.iterdir() if (path / "manifest.json").is_file()]

            automa("update", "perception", "--id", VEHICLE_ID, "--plugin", "frame")
            replay = automa("perception", "inspect", str(recording), "--json")
            self.assertEqual(_frame_label(json.loads(replay.stdout)), "release camera frame")

            releases = runtime_root / VEHICLE_ID / "bundle/releases"
            Path(release["archive"]["path"]).unlink()
            Path(release["manifest"]["path"]).unlink()
            shutil.rmtree(releases / release["archive"]["sha256"])
            missing = automa("perception", "inspect", str(recording), "--json", check=False)
            self.assertEqual(missing.returncode, 2, missing.stdout)
            self.assertIn(f"Controller release {release['tree_sha256'][:12]} is not in", missing.stdout)
            self.assertIn(f"Run: ./cli/automa vehicles update autonomy --id {VEHICLE_ID}", missing.stdout)


if __name__ == "__main__":
    unittest.main()
