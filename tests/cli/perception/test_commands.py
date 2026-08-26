from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
from cli.automa_cli import perception


ROOT = Path(__file__).resolve().parents[3]


class PerceptionCommandTests(unittest.TestCase):
    def test_piracer_staged_inspection_enriches_with_reachable_live_observation(self) -> None:
        vehicle = {
            "vehicle_id": "piracer",
            "provider": "picar",
            "connection": {"base_url": "http://piracer.local:8887"},
        }
        publication = {
            "schema": "automa_physical_observation_publication_v0",
            "ok": True,
            "health": "healthy",
            "algorithm": "lightweight_observer",
            "mode": "user",
            "result_age_ms": 120,
            "duration_ms": 280,
            "control": {"steering": 0.0, "throttle": 0.0, "reason": "stable-idle-engine"},
            "frame": {"frame_id": "donkey_frame_000011", "has_image": True},
        }
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            write_runtime_fixture(runtime_root, "piracer", pid=os.getpid(), manifest_bundle_root=ROOT)
            with patch.object(perception, "RUNTIME_ROOT", runtime_root), patch.object(
                perception,
                "discover_active_vehicles",
                return_value={"vehicles": [vehicle]},
            ) as discover, patch.object(
                perception,
                "physical_view_status",
                return_value={"available": True, "url": "http://127.0.0.1:9100"},
            ) as view_status, patch.object(
                perception,
                "fetch_observation_publication",
                return_value=publication,
            ) as fetch:
                json_result = perception.get_vehicle_perception_info(
                    vehicle_id="piracer", json_output=True, timeout_s=1.25
                )
                text_result = perception.get_vehicle_perception_info(
                    vehicle_id="piracer", json_output=False, timeout_s=1.25
                )

        self.assertEqual(json_result.exit_code, 0)
        payload = json.loads(json_result.message)
        self.assertEqual(payload["activation"]["algorithm"], "sim_debug")
        self.assertTrue(payload["live_observation"]["available"])
        self.assertEqual(payload["live_observation"]["provider"], "picar")
        self.assertEqual(payload["live_observation"]["frame"]["frame_id"], "donkey_frame_000011")
        self.assertTrue(payload["published_view"]["available"])
        self.assertIn("Live onboard observation:", text_result.message)
        self.assertIn("local view: http://127.0.0.1:9100", text_result.message)
        self.assertEqual(discover.call_count, 2)
        self.assertEqual(view_status.call_count, 2)
        self.assertEqual(fetch.call_count, 2)
        fetch.assert_called_with("http://piracer.local:8887", timeout_s=1.25)

    def test_staged_inspection_survives_unavailable_live_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            write_runtime_fixture(runtime_root, "piracer", pid=os.getpid(), manifest_bundle_root=ROOT)
            with patch.object(perception, "RUNTIME_ROOT", runtime_root), patch.object(
                perception,
                "discover_active_vehicles",
                return_value={"vehicles": []},
            ) as discover:
                result = perception.get_vehicle_perception_info(
                    vehicle_id="piracer", json_output=True, timeout_s=0.5
                )
                text_result = perception.get_vehicle_perception_info(
                    vehicle_id="piracer", json_output=False, timeout_s=0.5
                )

        self.assertEqual(result.exit_code, 0)
        payload = json.loads(result.message)
        self.assertEqual(payload["activation"]["algorithm"], "sim_debug")
        self.assertFalse(payload["live_observation"]["available"])
        self.assertIn("not found among discoverable vehicles", payload["live_observation"]["reason"])
        self.assertIn("Live onboard observation: unavailable", text_result.message)
        self.assertEqual(discover.call_count, 2)

    def test_staged_inspection_keeps_live_observation_when_view_is_unavailable(self) -> None:
        vehicle = {
            "vehicle_id": "piracer",
            "provider": "picar",
            "connection": {"base_url": "http://piracer.local:8887"},
        }
        publication = {
            "health": "healthy",
            "frame": {"frame_id": "frame_view_unavailable"},
            "control": {"steering": 0.0, "throttle": 0.0},
        }
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            write_runtime_fixture(runtime_root, "piracer", pid=os.getpid(), manifest_bundle_root=ROOT)
            with patch.object(perception, "RUNTIME_ROOT", runtime_root), patch.object(
                perception,
                "discover_active_vehicles",
                return_value={"vehicles": [vehicle]},
            ), patch.object(
                perception,
                "physical_view_status",
                return_value={"available": False, "reason": "view not started"},
            ), patch.object(
                perception,
                "fetch_observation_publication",
                return_value=publication,
            ):
                result = perception.get_vehicle_perception_info(
                    vehicle_id="piracer", json_output=True, timeout_s=0.5
                )
                text_result = perception.get_vehicle_perception_info(
                    vehicle_id="piracer", json_output=False, timeout_s=0.5
                )

        payload = json.loads(result.message)
        self.assertEqual(result.exit_code, 0)
        self.assertTrue(payload["live_observation"]["available"])
        self.assertFalse(payload["live_observation"]["published_view"]["available"])
        self.assertEqual(payload["live_observation"]["published_view"]["reason"], "view not started")
        self.assertIn("local view: unavailable (view not started)", text_result.message)

    def test_staged_inspection_survives_discovery_connection_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            write_runtime_fixture(runtime_root, "piracer", pid=os.getpid(), manifest_bundle_root=ROOT)
            with patch.object(perception, "RUNTIME_ROOT", runtime_root), patch.object(
                perception,
                "discover_active_vehicles",
                side_effect=ConnectionError("discovery timed out"),
            ):
                result = perception.get_vehicle_perception_info(
                    vehicle_id="piracer", json_output=True, timeout_s=0.5
                )

        payload = json.loads(result.message)
        self.assertEqual(result.exit_code, 0)
        self.assertFalse(payload["live_observation"]["available"])
        self.assertIn("discovery timed out", payload["live_observation"]["reason"])

    def test_staged_inspection_reports_reachable_non_piracer_without_fabrication(self) -> None:
        chase = {
            "vehicle_id": "piracer",
            "provider": "chase-sim",
            "connection": {"ws_url": "ws://simulator/ws/control"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            write_runtime_fixture(runtime_root, "piracer", pid=os.getpid(), manifest_bundle_root=ROOT)
            with patch.object(perception, "RUNTIME_ROOT", runtime_root), patch.object(
                perception,
                "discover_active_vehicles",
                return_value={"vehicles": [chase]},
            ):
                result = perception.get_vehicle_perception_info(
                    vehicle_id="piracer", json_output=True, timeout_s=0.5
                )

        self.assertEqual(result.exit_code, 0)
        live = json.loads(result.message)["live_observation"]
        self.assertFalse(live["available"])
        self.assertEqual(live["provider"], "chase-sim")
        self.assertIn("not a PiRacer", live["reason"])

    def test_staged_inspection_bounds_unusable_picar_base_url(self) -> None:
        vehicle = {
            "vehicle_id": "piracer",
            "provider": "picar",
            "connection": {"base_url": "not-a-url"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            write_runtime_fixture(runtime_root, "piracer", pid=os.getpid(), manifest_bundle_root=ROOT)
            with patch.object(perception, "RUNTIME_ROOT", runtime_root), patch.object(
                perception,
                "discover_active_vehicles",
                return_value={"vehicles": [vehicle]},
            ), patch.object(perception, "physical_view_status") as view_status, patch.object(
                perception, "fetch_observation_publication"
            ) as fetch:
                result = perception.get_vehicle_perception_info(
                    vehicle_id="piracer", json_output=True, timeout_s=0.5
                )

        self.assertEqual(result.exit_code, 0)
        live = json.loads(result.message)["live_observation"]
        self.assertFalse(live["available"])
        self.assertIn("invalid picar base_url", live["error"])
        view_status.assert_not_called()
        fetch.assert_not_called()

    def test_staged_inspection_does_not_claim_unavailable_live_health(self) -> None:
        vehicle = {
            "vehicle_id": "piracer",
            "provider": "picar",
            "connection": {"base_url": "http://piracer.local:8887"},
        }
        publication = {
            "health": "unavailable",
            "error": "observation warming",
            "frame": None,
            "control": None,
        }
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            write_runtime_fixture(runtime_root, "piracer", pid=os.getpid(), manifest_bundle_root=ROOT)
            with patch.object(perception, "RUNTIME_ROOT", runtime_root), patch.object(
                perception,
                "discover_active_vehicles",
                return_value={"vehicles": [vehicle]},
            ), patch.object(
                perception,
                "physical_view_status",
                return_value={"available": False, "reason": "view not started"},
            ), patch.object(
                perception,
                "fetch_observation_publication",
                return_value=publication,
            ):
                result = perception.get_vehicle_perception_info(
                    vehicle_id="piracer", json_output=True, timeout_s=0.5
                )

        self.assertEqual(result.exit_code, 0)
        live = json.loads(result.message)["live_observation"]
        self.assertFalse(live["available"])
        self.assertEqual(live["error"], "observation warming")

    def test_no_local_staging_preserves_live_only_activation_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            with patch.object(perception, "RUNTIME_ROOT", runtime_root), patch.object(
                perception,
                "discover_active_vehicles",
                return_value={"vehicles": []},
            ):
                result = perception.get_vehicle_perception_info(
                    vehicle_id="piracer", json_output=True, timeout_s=0.5
                )

        self.assertEqual(result.exit_code, 2)
        self.assertIn("No active perception algorithm found", result.message)

    def test_no_local_staging_rejects_reachable_non_piracer(self) -> None:
        vehicle = {
            "vehicle_id": "piracer",
            "provider": "chase-sim",
            "connection": {"ws_url": "ws://simulator/ws/control"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            with patch.object(perception, "RUNTIME_ROOT", runtime_root), patch.object(
                perception,
                "discover_active_vehicles",
                return_value={"vehicles": [vehicle]},
            ):
                result = perception.get_vehicle_perception_info(
                    vehicle_id="piracer", json_output=True, timeout_s=0.5
                )

        self.assertEqual(result.exit_code, 2)
        self.assertIn("No active perception algorithm found", result.message)

    def test_local_activation_error_is_not_hidden_by_reachable_picar(self) -> None:
        vehicle = {
            "vehicle_id": "piracer",
            "provider": "picar",
            "connection": {"base_url": "http://piracer.local:8887"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            fixture = write_runtime_fixture(
                runtime_root, "piracer", pid=os.getpid(), manifest_bundle_root=ROOT
            )
            (fixture.bundle_root / "runtime" / "perception" / "active.json").write_text(
                "{}", encoding="utf-8"
            )
            with patch.object(perception, "RUNTIME_ROOT", runtime_root), patch.object(
                perception,
                "discover_active_vehicles",
                return_value={"vehicles": [vehicle]},
            ):
                result = perception.get_vehicle_perception_info(
                    vehicle_id="piracer", json_output=True, timeout_s=0.5
                )

        self.assertEqual(result.exit_code, 2)
        self.assertIn("does not define perception.mapper_spec", result.message)

    def test_piracer_without_local_staging_keeps_live_only_inspection(self) -> None:
        vehicle = {
            "vehicle_id": "piracer",
            "provider": "picar",
            "connection": {"base_url": "http://piracer.local:8887"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            with patch.object(perception, "RUNTIME_ROOT", runtime_root), patch.object(
                perception,
                "discover_active_vehicles",
                return_value={"vehicles": [vehicle]},
            ), patch.object(
                perception,
                "physical_view_status",
                return_value={"available": False, "reason": "view not started"},
            ), patch.object(
                perception,
                "fetch_observation_publication",
                return_value={
                    "health": "healthy",
                    "frame": {"frame_id": "frame_1"},
                    "control": {"steering": 0.0, "throttle": 0.0},
                },
            ):
                result = perception.get_vehicle_perception_info(
                    vehicle_id="piracer", json_output=True, timeout_s=0.5
                )

        payload = json.loads(result.message)
        self.assertEqual(result.exit_code, 0)
        self.assertIsNone(payload["activation"])
        self.assertTrue(payload["live_observation"]["available"])

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
            server.publish_frame(frame_path=frame_path, frame_record=frame_record)
            server.publish_perception(frame_record=frame_record)
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
