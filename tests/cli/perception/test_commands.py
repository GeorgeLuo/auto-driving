from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from cli.automa_cli.bundles import (
    controller_bundle_paths,
    release_activation_summary,
    sync_controller_bundle,
)
from cli.automa_cli.perception_view import PerceptionViewServer
from implementations.perception.catalog import (
    DEFAULT_PERCEPTION_ALGORITHM,
    PERCEPTION_ALGORITHMS,
    PERCEPTION_MAPPER_SPEC,
    PERCEPTION_PLUGIN_SPECS,
)
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
        self.assertEqual(payload["algorithm_schema"]["schema"], "perception_algorithm_schema_v2")
        self.assertEqual(payload["algorithm_schema"]["output"]["schema"], "perception_text_v2")
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
            automation_dir = runtime_root / vehicle_id / "bundle" / "runtime" / "automation"
            server = PerceptionViewServer(
                vehicle_id=vehicle_id,
                automation_dir=automation_dir,
                port=0,
            ).start()
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

    def test_perception_bundle_syncs_configured_visual_observer_plugins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            vehicle_id = "chase-sim-chaser"
            vehicle_runtime_dir = runtime_root / vehicle_id
            bundle = controller_bundle_paths(vehicle_runtime_dir)
            release = sync_controller_bundle(bundle, output=None)

            bundle_root = Path(bundle["root_dir"])
            archive_path = Path(release["archive"]["path"])
            manifest_path = Path(release["manifest"]["path"])
            latest_path = bundle_root / "releases" / "latest-controller-bundle.json"
            self.assertTrue(archive_path.exists())
            self.assertTrue(manifest_path.exists())
            self.assertTrue(latest_path.exists())
            release_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(release_manifest["tree_sha256"], release["tree_sha256"])
            self.assertEqual(release_manifest["archive"]["sha256"], release["archive"]["sha256"])
            self.assertEqual(release_manifest["file_count"], release["file_count"])
            self.assertEqual(
                [source["package_root"] for source in release_manifest["sources"]],
                ["autonomy", "implementations"],
            )

            for relative in (
                "implementations/perception/traversability/plugin.py",
                "implementations/perception/preparation/vlm.py",
                "implementations/perception/motion/tracks.py",
                "autonomy/perception/mappers/plugin_runner.py",
                "bundle-manifest.json",
            ):
                self.assertTrue((bundle_root / relative).exists(), relative)

            perception_dir = bundle_root / "runtime" / "perception"
            algorithm_config = PERCEPTION_ALGORITHMS["visual_observer"]
            write_json(
                perception_dir / "active.json",
                {
                    "schema": "automa_perception_activation_v0",
                    "vehicle_id": vehicle_id,
                    "vehicle_kind": "chase-sim-ws",
                    "provider": "chase-sim",
                    "controller_bundle": {
                        **bundle,
                        "release": release_activation_summary(release),
                    },
                    "perception": {
                        "algorithm": "visual_observer",
                        "mapper_spec": PERCEPTION_MAPPER_SPEC,
                        "mapper_config": dict(algorithm_config["mapper_config"]),
                        "source_dir": bundle["perception_dir"],
                    },
                },
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
            text_result = run_automa(
                "vehicles",
                "info",
                "perception",
                "--id",
                vehicle_id,
                runtime_root=runtime_root,
            )

        payload = json.loads(json_result.stdout)
        self.assertEqual(payload["activation"]["algorithm"], "visual_observer")
        self.assertEqual(payload["controller_bundle"]["release"]["tree_sha256"], release_manifest["tree_sha256"])
        self.assertEqual(
            payload["activation"]["mapper_config"]["plugins"],
            ["frame", "floor_plane", "motion_tracks"],
        )
        chain = payload["algorithm_schema"]["plugins"]
        self.assertEqual(
            [plugin["plugin_id"] for plugin in chain],
            [
                "frame-observation-v0",
                "floor-plane-v0",
                "motion-tracks-v0",
            ],
        )
        self.assertIn("Enabled plugins: frame, floor_plane, motion_tracks", text_result.stdout)
        self.assertIn("Plugins:", text_result.stdout)
        self.assertIn(
            "frame-observation-v0 [stateless] components=camera.rgb:front_camera",
            text_result.stdout,
        )

    def test_perception_update_dry_run_json_does_not_require_live_simulator(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            result = run_automa(
                "vehicles",
                "update",
                "perception",
                "--id",
                "chase-sim-chaser",
                "--dry-run",
                "--json",
                runtime_root=runtime_root,
            )

        payload = json.loads(result.stdout)
        self.assertEqual(payload["schema"], "vehicle_perception_update_v0")
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["vehicle_id"], "chase-sim-chaser")
        self.assertEqual(payload["algorithm"], DEFAULT_PERCEPTION_ALGORITHM)
        self.assertEqual(payload["manifest"]["provider"], "chase-sim")
        self.assertTrue(payload["would_write"]["bundle_root"].endswith("vehicles/chase-sim-chaser/bundle"))

    def test_ready_lab_candidate_can_be_staged_and_inspected_locally(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime_root = root / "vehicles"
            candidate_root = root / "candidates"
            candidate_dir = candidate_root / "fixture"
            write_json(
                candidate_dir / "plugin.json",
                {
                    "schema": "automa_lab_perception_plugin_v0",
                    "id": "fixture",
                    "name": "Fixture regions",
                    "description": "Test-only isolated candidate.",
                    "plugin": {
                        "entrypoint": (
                            "implementations.perception.observation.plugin:"
                            "FrameObservationPlugin"
                        ),
                        "config": {},
                    },
                    "runtime": {"python": "core"},
                    "output": {
                        "schema": "perception_text_v2",
                        "kind": "sensor_frame",
                        "semantic_labels": False,
                        "depth": False,
                    },
                },
            )
            env = {"AUTOMA_LAB_PERCEPTION_ROOT": str(candidate_root)}

            update = run_automa(
                "vehicles",
                "update",
                "perception",
                "--id",
                "chase-sim-chaser",
                "--candidate",
                "fixture",
                "--json",
                runtime_root=runtime_root,
                extra_env=env,
            )
            info = run_automa(
                "vehicles",
                "info",
                "perception",
                "--id",
                "chase-sim-chaser",
                "--json",
                runtime_root=runtime_root,
                extra_env=env,
            )
            text_info = run_automa(
                "vehicles",
                "info",
                "perception",
                "--id",
                "chase-sim-chaser",
                runtime_root=runtime_root,
                extra_env=env,
            )

        update_payload = json.loads(update.stdout)
        info_payload = json.loads(info.stdout)
        self.assertEqual(update_payload["algorithm"], "candidate:fixture")
        self.assertEqual(
            update_payload["manifest"]["perception"]["mapper_spec"],
            "cli.automa_cli.lab_plugins:LabPerceptionMapper",
        )
        self.assertEqual(
            update_payload["manifest"]["perception"]["mapper_config"]["candidate_id"],
            "fixture",
        )
        self.assertTrue(
            update_payload["manifest"]["perception"]["candidate"]["source_tree_sha256"]
        )
        self.assertEqual(info_payload["activation"]["algorithm"], "candidate:fixture")
        self.assertEqual(info_payload["algorithm_schema"]["candidate"]["id"], "fixture")
        self.assertIn("Candidate: fixture (isolated local runtime)", text_info.stdout)
        self.assertNotIn("Enabled plugins: none", text_info.stdout)

    def test_lab_candidate_cannot_be_staged_for_physical_vehicle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime_root = root / "vehicles"
            bundle = controller_bundle_paths(runtime_root / "piracer")
            write_json(
                Path(bundle["perception_runtime_dir"]) / "active.json",
                {
                    "schema": "automa_perception_activation_v0",
                    "vehicle_id": "piracer",
                    "vehicle_kind": "picar",
                    "provider": "picar",
                    "runtime": {"kind": "onboard_controller", "connection": {}},
                    "controller_bundle": bundle,
                    "perception": {
                        "algorithm": "lightweight_observer",
                        "mapper_spec": PERCEPTION_MAPPER_SPEC,
                        "mapper_config": {},
                    },
                },
            )
            candidate_root = root / "candidates"
            write_json(
                candidate_root / "fixture" / "plugin.json",
                {
                    "schema": "automa_lab_perception_plugin_v0",
                    "id": "fixture",
                    "plugin": {
                        "entrypoint": (
                            "implementations.perception.observation.plugin:"
                            "FrameObservationPlugin"
                        ),
                        "config": {},
                    },
                    "runtime": {"python": "core"},
                    "output": {"schema": "perception_text_v2"},
                },
            )

            result = run_automa(
                "vehicles",
                "update",
                "perception",
                "--id",
                "piracer",
                "--candidate",
                "fixture",
                runtime_root=runtime_root,
                extra_env={"AUTOMA_LAB_PERCEPTION_ROOT": str(candidate_root)},
                check=False,
            )

        self.assertEqual(result.returncode, 2)
        self.assertIn("can only be activated for a Chase simulator vehicle", result.stdout)

    def test_physical_perception_staging_reuses_local_metadata_while_offline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            bundle = controller_bundle_paths(runtime_root / "piracer")
            sync_controller_bundle(bundle, output=None)
            write_json(
                Path(bundle["perception_runtime_dir"]) / "active.json",
                {
                    "schema": "automa_perception_activation_v0",
                    "vehicle_id": "piracer",
                    "vehicle_kind": "picar",
                    "provider": "picar",
                    "runtime": {"kind": "onboard_controller", "connection": {}},
                    "controller_bundle": bundle,
                    "perception": {
                        "algorithm": "lightweight_observer",
                        "mapper_spec": PERCEPTION_MAPPER_SPEC,
                        "mapper_config": dict(
                            PERCEPTION_ALGORITHMS["lightweight_observer"]["mapper_config"]
                        ),
                    },
                },
            )

            result = run_automa(
                "vehicles",
                "update",
                "perception",
                "--id",
                "piracer",
                "--algorithm",
                "visual_observer",
                "--json",
                runtime_root=runtime_root,
            )

        payload = json.loads(result.stdout)
        self.assertEqual(payload["vehicle_id"], "piracer")
        self.assertEqual(payload["algorithm"], "visual_observer")
        self.assertEqual(payload["manifest"]["provider"], "picar")

    def test_perception_plugin_enable_disable_edits_active_activation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            vehicle_id = "chase-sim-chaser"
            vehicle_runtime_dir = runtime_root / vehicle_id
            bundle = controller_bundle_paths(vehicle_runtime_dir)
            sync_controller_bundle(bundle, output=None)

            perception_dir = Path(bundle["root_dir"]) / "runtime" / "perception"
            write_json(
                perception_dir / "active.json",
                {
                    "schema": "automa_perception_activation_v0",
                    "vehicle_id": vehicle_id,
                    "vehicle_kind": "chase-sim-ws",
                    "provider": "chase-sim",
                    "controller_bundle": bundle,
                    "perception": {
                        "algorithm": "lightweight_observer",
                        "mapper_spec": PERCEPTION_MAPPER_SPEC,
                        "mapper_config": {
                            "plugins": ["frame"],
                            "plugin_specs": dict(PERCEPTION_PLUGIN_SPECS),
                        },
                        "source_dir": bundle["perception_dir"],
                    },
                },
            )

            enable = run_automa(
                "vehicles",
                "perception",
                "enable",
                "--id",
                vehicle_id,
                "floor_plane",
                "--json",
                runtime_root=runtime_root,
            )
            disable = run_automa(
                "vehicles",
                "perception",
                "disable",
                "--id",
                vehicle_id,
                "frame",
                "--json",
                runtime_root=runtime_root,
            )
            info = run_automa(
                "vehicles",
                "info",
                "perception",
                "--id",
                vehicle_id,
                "--json",
                runtime_root=runtime_root,
            )

        enable_payload = json.loads(enable.stdout)
        self.assertTrue(enable_payload["changed"])
        self.assertEqual(enable_payload["plugins_after"], ["frame", "floor_plane"])

        disable_payload = json.loads(disable.stdout)
        self.assertTrue(disable_payload["changed"])
        self.assertEqual(disable_payload["plugins_after"], ["floor_plane"])

        info_payload = json.loads(info.stdout)
        self.assertEqual(info_payload["activation"]["algorithm"], "custom")
        self.assertEqual(info_payload["activation"]["mapper_config"]["plugins"], ["floor_plane"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
