"""Replay runs the code a recording names, not today's code.

The Chase host runs from a release whose frame plugin differs from this
checkout, as one built from another commit would. After an update from the
checkout, replaying the recording must still show that release's behaviour.
A plugin uploaded to the host is outside every release; the recording carries
its source, so a fresh process replays it.
"""
from __future__ import annotations

from functools import partial
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from tests.support.runtime_fixtures import stage_step, write_host_fixture
from unittest import mock

from cli.automa_cli.hosts import bundles
from cli.automa_cli.hosts.runtime_hosts import stop_chase_host
from tests.shared.cli.invoke import invoke_automa
from tests.support.cli_runner import WORKSPACE_ROOT, run_automa
from tests.support.fake_metrics_ui import fake_metrics_ui_server

HOST_NAME = "chase-sim-chaser"
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
        return bundles.package_controller_bundle(bundles.controller_bundle_paths(runtime_root / HOST_NAME))


def _frame_label(report: dict) -> str:
    things = report["frames"][0]["record"]["things"]
    return next(item for item in things if item["thing_id"] == "front_camera_frame")["label"]


def _automa(*args: str, runtime_root: Path, env: dict, check: bool = True):
    # The run starts a worker; replay needs a fresh import of recorded plugin code.
    own_process = args[:1] in (("run",), ("inspect",))
    run = run_automa if own_process else invoke_automa
    group = "plugins" if args[:1] in (("inspect",), ("upload",)) else "hosts"
    return run(group, *args, runtime_root=runtime_root, extra_env=env, check=check)


class ReplayByReleaseTests(unittest.TestCase):
    def test_a_recording_replays_on_its_release_after_an_update(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, fake_metrics_ui_server() as ws_url:
            runtime_root = Path(tmp) / "vehicles"
            env = {"CHASE_UI_WS_URL": ws_url}
            write_host_fixture(runtime_root, "chase-sim-chaser", url=ws_url)

            automa = partial(_automa, runtime_root=runtime_root, env=env)

            stage_step(runtime_root, HOST_NAME, "perception", plugins=["frame"])
            release = _package_release_with_frame_label(runtime_root, Path(tmp) / "source", "release camera frame")
            try:
                automa(
                    "run", "--name", HOST_NAME, "--observe-only",
                    "--num-decisions", "2", "--record",
                )
            finally:
                automa("stop", "--name", HOST_NAME, check=False)
            runs = runtime_root / HOST_NAME / "bundle/runtime/automation/runs"
            (recording,) = [path for path in runs.iterdir() if (path / "manifest.json").is_file()]

            automa("update", "--name", HOST_NAME)
            replay = automa("inspect", "perception", str(recording), "--json")
            self.assertEqual(_frame_label(json.loads(replay.stdout)), "release camera frame")

            releases = runtime_root / HOST_NAME / "bundle/releases"
            Path(release["archive"]["path"]).unlink()
            Path(release["manifest"]["path"]).unlink()
            shutil.rmtree(releases / release["archive"]["sha256"])
            missing = automa("inspect", "perception", str(recording), "--json", check=False)
            self.assertEqual(missing.returncode, 2, missing.stdout)
            self.assertIn(f"Controller release {release['tree_sha256'][:12]} is not in", missing.stdout)
            self.assertIn(f"Run: ./cli/automa hosts update --name {HOST_NAME}", missing.stdout)

    def test_a_recording_replays_the_plugin_uploaded_to_its_host(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, fake_metrics_ui_server() as ws_url:
            runtime_root = Path(tmp) / "vehicles"
            env = {"CHASE_UI_WS_URL": ws_url}
            write_host_fixture(runtime_root, "chase-sim-chaser", url=ws_url)

            automa = partial(_automa, runtime_root=runtime_root, env=env)

            stage_step(runtime_root, HOST_NAME, "perception", plugins=["frame"])
            bundles.package_controller_bundle(bundles.controller_bundle_paths(runtime_root / HOST_NAME))
            plugin = Path(tmp) / "prototype.py"
            plugin.write_text(
                "from dataclasses import replace\n"
                "from implementations.decision_cycle.perception.plugins.frame.plugin import FrameObservationPlugin\n"
                "class Prototype(FrameObservationPlugin):\n"
                "    plugin_id = 'uploaded'\n"
                "    def perceive(self, inputs):\n"
                "        batch = super().perceive(inputs)\n"
                "        return replace(batch, measurements={**batch.measurements, 'uploaded_revision': 7})\n",
                encoding="utf-8",
            )
            source = f"{hashlib.sha256(plugin.read_bytes()).hexdigest()}.py"
            try:
                automa("run", "--name", HOST_NAME, "--observe-only",
                       "--num-decisions", "1")
                automa("upload", "--host-name", HOST_NAME, "--file", str(plugin), "--name", "perception/uploaded",
                       "--entrypoint", "prototype:Prototype", "--arm")
                automa("run", "--name", HOST_NAME, "--observe-only",
                       "--num-decisions", "2", "--record")
            finally:
                automa("stop", "--name", HOST_NAME, check=False)
            runs = runtime_root / HOST_NAME / "bundle/runtime/automation/runs"
            (recording,) = [path for path in runs.iterdir() if (path / "manifest.json").is_file()]
            self.assertEqual((recording / "plugins" / source).read_bytes(), plugin.read_bytes())
            with mock.patch("cli.automa_cli.hosts.runtime_hosts.RUNTIME_ROOT", runtime_root):
                stop_chase_host(HOST_NAME)
            plugin.unlink()
            moved = Path(tmp) / "moved-recording"
            shutil.move(recording, moved)
            recording = moved

            replay = json.loads(automa("inspect", "perception", str(recording), "--json").stdout)
            for frame in replay["frames"]:
                perception = frame["record"]
                self.assertEqual([(run["plugin_id"], run["status"]) for run in perception["plugin_runs"]],
                                 [("frame", "ok"), ("uploaded", "ok")])
                self.assertEqual(perception["measurements"]["uploaded"]["uploaded_revision"], 7)

            env["AUTOMA_PERCEPTION_INSPECT_ROOT"] = str(Path(tmp) / "inspect-runs")
            archived = json.loads(automa("inspect", "perception", str(recording), "--record", "--json").stdout)
            shutil.rmtree(recording)
            recording = Path(tmp) / "moved-inspect"
            shutil.move(archived["run_dir"], recording)
            rerun = json.loads(automa("inspect", "perception", str(recording), "--json").stdout)
            self.assertEqual(rerun["frames"][0]["record"]["measurements"]["uploaded"]["uploaded_revision"], 7)

            shutil.rmtree(recording / "plugins")
            missing = automa("inspect", "perception", str(recording), "--json", check=False)
            self.assertEqual(missing.returncode, 2, missing.stdout)
            self.assertIn(f"uploaded plugin source {source} is not available here", missing.stdout)


if __name__ == "__main__":
    unittest.main()
