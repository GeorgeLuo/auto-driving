from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from cli.automa_cli.bundles import (
    controller_bundle_paths,
    release_activation_summary,
    sync_controller_bundle,
)
from cli.automa_cli.perception import CURRENT_MAPPER_SPEC, PERCEPTION_ALGORITHMS, PERCEPTION_PLUGIN_SPECS


ROOT = Path(__file__).resolve().parents[2]
AUTOMA = ROOT / "cli" / "automa"


class AutomaCliHarness(unittest.TestCase):
    def run_automa(
        self,
        *args: str,
        runtime_root: Path | None = None,
        extra_env: dict[str, str] | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        env = {
            **os.environ,
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        if runtime_root is not None:
            env["AUTOMA_RUNTIME_ROOT"] = str(runtime_root)
        if extra_env is not None:
            env.update(extra_env)
        result = subprocess.run(
            [sys.executable, str(AUTOMA), *args],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if check and result.returncode != 0:
            self.fail(
                "\n".join(
                    [
                        f"automa {' '.join(args)} failed with exit code {result.returncode}",
                        "stdout:",
                        result.stdout,
                        "stderr:",
                        result.stderr,
                    ]
                )
            )
        return result

    def test_top_level_help_explains_purpose_without_nested_usage(self) -> None:
        result = self.run_automa("help")

        self.assertIn("Automa is the control desk", result.stdout)
        self.assertIn("- vehicles", result.stdout)
        self.assertIn("- simulators", result.stdout)
        self.assertNotIn("automa commands", result.stdout)
        self.assertNotIn("vehicles automation run", result.stdout)

    def test_vehicles_help_shows_only_vehicle_level_commands(self) -> None:
        result = self.run_automa("vehicles")

        self.assertIn("- active", result.stdout)
        self.assertIn("- update", result.stdout)
        self.assertIn("- automation", result.stdout)
        self.assertIn("- perception", result.stdout)
        self.assertNotIn("./cli/automa vehicles automation run", result.stdout)
        self.assertNotIn("./cli/automa vehicles update perception", result.stdout)

    def test_nested_help_shows_only_that_level(self) -> None:
        result = self.run_automa("vehicles", "automation", "help")

        self.assertIn("automa vehicles automation commands", result.stdout)
        self.assertIn("- run", result.stdout)
        self.assertIn("- status", result.stdout)
        self.assertNotIn("--interval-s", result.stdout)

    def test_perception_help_shows_only_perception_level_commands(self) -> None:
        result = self.run_automa("vehicles", "perception", "help")

        self.assertIn("automa vehicles perception commands", result.stdout)
        self.assertIn("- enable", result.stdout)
        self.assertIn("- disable", result.stdout)
        self.assertNotIn("--id", result.stdout)

    def test_operation_help_shows_only_bounded_operations(self) -> None:
        result = self.run_automa("vehicles", "operation", "help")

        self.assertIn("automa vehicles operation commands", result.stdout)
        self.assertIn("- startup-check", result.stdout)
        self.assertNotIn("--throttle", result.stdout)

    def test_simulators_help_shows_only_simulator_level_commands(self) -> None:
        result = self.run_automa("simulators")

        self.assertIn("automa simulators commands", result.stdout)
        self.assertIn("- status", result.stdout)
        self.assertIn("- ensure", result.stdout)
        self.assertNotIn("--timeout-ms", result.stdout)

    def test_simulator_status_json_online(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = self.make_fake_simeval(Path(tmp), "online")
            result = self.run_automa("simulators", "status", "--json", extra_env=env)

        payload = json.loads(result.stdout)
        self.assertEqual(payload["schema"], "automa_simulator_status_v0")
        self.assertTrue(payload["status"]["online"])
        self.assertEqual(payload["status"]["online_count"], 1)
        self.assertTrue(payload["frontend"]["frontend_connected"])

    def test_simulator_ensure_reuses_online_deployment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self.make_fake_simeval(root, "online")
            result = self.run_automa("simulators", "ensure", "--json", extra_env=env)
            calls = self.read_fake_simeval_calls(root)

        payload = json.loads(result.stdout)
        self.assertTrue(payload["result"]["usable"])
        self.assertFalse(payload["result"]["launched"])
        self.assertNotIn(["deploy", "start"], calls)
        self.assertIn(["ui", "subapp", "--app", "play"], calls)
        self.assertIn(
            ["ui", "play-game-action", "--action-id", "scenario-select", "--value", '"default"'],
            calls,
        )

    def test_simulator_ensure_opens_browser_when_frontend_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {
                **self.make_fake_simeval(root, "online_no_frontend_then_open"),
                **self.make_fake_browser(root),
            }
            result = self.run_automa("simulators", "ensure", "--json", extra_env=env)
            browser_calls = self.read_fake_browser_calls(root)

        payload = json.loads(result.stdout)
        self.assertTrue(payload["result"]["usable"])
        self.assertTrue(payload["frontend"]["browser_open"]["attempted"])
        self.assertTrue(payload["frontend"]["after"]["frontend_connected"])
        self.assertEqual(browser_calls, ["http://127.0.0.1:5050"])

    def test_simulator_ensure_reopens_stale_frontend_when_play_commands_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {
                **self.make_fake_simeval(root, "online_frontend_stale_until_open"),
                **self.make_fake_browser(root),
            }
            result = self.run_automa("simulators", "ensure", "--json", extra_env=env)
            browser_calls = self.read_fake_browser_calls(root)
            calls = self.read_fake_simeval_calls(root)

        payload = json.loads(result.stdout)
        self.assertTrue(payload["result"]["usable"])
        self.assertTrue(payload["frontend"]["browser_open"]["attempted"])
        self.assertEqual(browser_calls, ["http://127.0.0.1:5050"])
        self.assertGreaterEqual(calls.count(["ui", "play-debug", "--summary"]), 2)

    def test_simulator_ensure_launches_when_offline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self.make_fake_simeval(root, "offline_then_launch")
            result = self.run_automa("simulators", "ensure", "--json", extra_env=env)
            calls = self.read_fake_simeval_calls(root)

        payload = json.loads(result.stdout)
        self.assertTrue(payload["result"]["usable"])
        self.assertTrue(payload["result"]["launched"])
        self.assertIn(["deploy", "start"], calls)

    def test_simulator_ensure_reports_launch_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = self.make_fake_simeval(Path(tmp), "launch_fails")
            result = self.run_automa("simulators", "ensure", extra_env=env, check=False)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("launch attempted: yes", result.stdout)
        self.assertIn("launched: no", result.stdout)
        self.assertIn("usable: no", result.stdout)
        self.assertIn("simeval deploy start failed", result.stdout)

    def test_scenario_first_time_discovery_can_return_machine_readable_empty_snapshot(self) -> None:
        result = self.run_automa("vehicles", "active", "--no-picar", "--no-sim", "--json")

        payload = json.loads(result.stdout)
        self.assertEqual(payload["schema"], "automa_vehicle_discovery_v0")
        self.assertEqual(payload["active_count"], 0)
        self.assertEqual(payload["vehicles"], [])
        self.assertEqual(payload["discovery"]["candidate_count"], 0)
        self.assertEqual(payload["inactive"], [])

    def test_vehicles_active_rejects_removed_compatibility_aliases(self) -> None:
        for removed_flag in ("--verbose", "--include-inactive"):
            with self.subTest(flag=removed_flag):
                result = self.run_automa(
                    "vehicles",
                    "active",
                    "--no-picar",
                    "--no-sim",
                    removed_flag,
                    check=False,
                )

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("unrecognized arguments", result.stderr)

        result = self.run_automa(
            "vehicles",
            "automation",
            "run",
            "--id",
            "chase-sim-chaser",
            "--prepare-control",
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unrecognized arguments", result.stderr)

    def test_automation_status_empty_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            result = self.run_automa("vehicles", "automation", "status", runtime_root=runtime_root)

        self.assertIn("deployed automations: 0", result.stdout)
        self.assertIn("No deployed automation runtimes found.", result.stdout)

    def test_automation_status_reads_fake_deployment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            self.write_fake_deployment(runtime_root, "chase-sim-chaser", pid=os.getpid())

            result = self.run_automa(
                "vehicles",
                "automation",
                "status",
                "--id",
                "chase-sim-chaser",
                runtime_root=runtime_root,
            )

        self.assertIn("deployed automations: 1", result.stdout)
        self.assertIn("chase-sim-chaser", result.stdout)
        self.assertIn("perception: current", result.stdout)
        self.assertIn("worker: running", result.stdout)
        self.assertIn("log: disabled", result.stdout)

    def test_scenario_status_distinguishes_stale_worker_pid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            self.write_fake_deployment(runtime_root, "chase-sim-chaser", pid=999_999_999)

            result = self.run_automa(
                "vehicles",
                "automation",
                "status",
                "--id",
                "chase-sim-chaser",
                "--json",
                runtime_root=runtime_root,
            )

        payload = json.loads(result.stdout)
        process = payload["vehicles"][0]["process"]
        self.assertFalse(process["running"])
        self.assertEqual(process["pid_state"], "not_running")

    def test_scenario_stop_stale_worker_marks_runtime_stopped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            self.write_fake_deployment(runtime_root, "chase-sim-chaser", pid=999_999_999)

            stop = self.run_automa(
                "vehicles",
                "automation",
                "stop",
                "--id",
                "chase-sim-chaser",
                runtime_root=runtime_root,
            )
            status = self.run_automa(
                "vehicles",
                "automation",
                "status",
                "--id",
                "chase-sim-chaser",
                "--json",
                runtime_root=runtime_root,
            )

        self.assertIn("Automation is not running", stop.stdout)
        payload = json.loads(status.stdout)
        state = payload["vehicles"][0]["state"]
        self.assertEqual(state["status"], "stopped")

    def test_automation_status_json_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            self.write_fake_deployment(runtime_root, "chase-sim-chaser", pid=os.getpid())

            result = self.run_automa(
                "vehicles",
                "automation",
                "status",
                "--id",
                "chase-sim-chaser",
                "--json",
                runtime_root=runtime_root,
            )

        payload = json.loads(result.stdout)
        self.assertEqual(payload["schema"], "automa_automation_status_v0")
        self.assertEqual(len(payload["vehicles"]), 1)
        vehicle = payload["vehicles"][0]
        self.assertEqual(vehicle["vehicle_id"], "chase-sim-chaser")
        self.assertTrue(vehicle["deployed"])
        self.assertEqual(vehicle["perception"]["algorithm"], "current")
        self.assertEqual(vehicle["decision"]["engine_id"], "idle")
        self.assertTrue(vehicle["process"]["running"])

    def test_decision_update_and_info_use_engine_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            update = self.run_automa(
                "vehicles",
                "update",
                "decision",
                "--id",
                "chase-sim-chaser",
                "--engine",
                "idle",
                "--json",
                runtime_root=runtime_root,
            )
            info = self.run_automa(
                "vehicles",
                "info",
                "decision",
                "--id",
                "chase-sim-chaser",
                "--json",
                runtime_root=runtime_root,
            )

        update_payload = json.loads(update.stdout)
        self.assertEqual(update_payload["schema"], "vehicle_decision_update_v0")
        self.assertEqual(update_payload["manifest"]["decision"]["engine_id"], "idle")
        self.assertIsNotNone(update_payload["release"]["tree_sha256"])

        info_payload = json.loads(info.stdout)
        self.assertEqual(info_payload["schema"], "vehicle_decision_info_v0")
        self.assertEqual(info_payload["activation"]["engine_id"], "idle")
        self.assertEqual(info_payload["engine_schema"]["schema"], "autonomy_engine_schema_v0")
        self.assertEqual(info_payload["engine_schema_source"]["method"], "describe_schema")

    def test_decision_update_dry_run_does_not_write_activation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            result = self.run_automa(
                "vehicles",
                "update",
                "decision",
                "--id",
                "chase-sim-chaser",
                "--dry-run",
                "--json",
                runtime_root=runtime_root,
            )

            payload = json.loads(result.stdout)
            activation = runtime_root / "chase-sim-chaser" / "bundle" / "runtime" / "decision" / "active.json"
            self.assertTrue(payload["dry_run"])
            self.assertFalse(activation.exists())

    def test_scenario_deployed_perception_schema_is_machine_readable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            self.write_fake_deployment(
                runtime_root,
                "chase-sim-chaser",
                pid=os.getpid(),
                bundle_root=ROOT,
            )

            result = self.run_automa(
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
        self.assertEqual(payload["activation"]["algorithm"], "current")
        self.assertEqual(payload["algorithm_schema"]["schema"], "perception_algorithm_schema_v0")
        self.assertEqual(payload["algorithm_schema"]["output"]["schema"], "perception_text_v0")

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
                "implementations/perception/floor_plane.py",
                "implementations/perception/vlm_prep.py",
                "implementations/perception/motion_groups.py",
                "autonomy/perception/mappers/current.py",
                "bundle-manifest.json",
            ):
                self.assertTrue((bundle_root / relative).exists(), relative)

            perception_dir = bundle_root / "runtime" / "perception"
            algorithm_config = PERCEPTION_ALGORITHMS["visual_observer"]
            self.write_json(
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
                        "mapper_spec": CURRENT_MAPPER_SPEC,
                        "mapper_config": dict(algorithm_config["mapper_config"]),
                        "source_dir": bundle["perception_dir"],
                    },
                },
            )

            json_result = self.run_automa(
                "vehicles",
                "info",
                "perception",
                "--id",
                vehicle_id,
                "--json",
                runtime_root=runtime_root,
            )
            text_result = self.run_automa(
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
            ["frame", "floor_plane", "vlm_prep", "motion_groups"],
        )
        chain = payload["algorithm_schema"]["inputs"][0]["plugin_chain"]
        self.assertEqual(
            [plugin["plugin_id"] for plugin in chain],
            [
                "frame-observation-v0",
                "floor-plane-v0",
                "vlm-prep-v0",
                "motion-groups-v0",
            ],
        )
        self.assertIn("Enabled plugins: frame, floor_plane, vlm_prep, motion_groups", text_result.stdout)
        self.assertIn("plugin chain:", text_result.stdout)

    def test_perception_update_dry_run_json_does_not_require_live_simulator(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            result = self.run_automa(
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
        self.assertEqual(payload["algorithm"], "current")
        self.assertEqual(payload["manifest"]["provider"], "chase-sim")
        self.assertTrue(payload["would_write"]["bundle_root"].endswith("vehicles/chase-sim-chaser/bundle"))

    def test_core_update_dry_run_can_skip_live_discovery(self) -> None:
        result = self.run_automa(
            "vehicles",
            "update",
            "core",
            "--id",
            "piracer",
            "--skip-discovery",
            "--ssh-target",
            "piracer@example.local",
            "--dry-run",
            "--restart",
            "--drive-args=--js",
        )

        self.assertIn("Core update dry run for piracer -> piracer@example.local", result.stdout)
        self.assertIn("would ensure DonkeyCar vendor source:", result.stdout)
        self.assertIn("deploy/targets/donkeycar/vendor/donkeycar", result.stdout)
        self.assertIn("deploy/targets/donkeycar/app", result.stdout)
        self.assertIn("--exclude=autonomy", result.stdout)
        self.assertIn("--exclude=implementations", result.stdout)
        self.assertIn("--exclude=runtime", result.stdout)
        self.assertIn("DRIVE_ARGS=--js scripts/deploy/donkeycar/restart_drive.sh", result.stdout)

    def test_autonomy_update_dry_run_is_versioned_and_machine_readable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            result = self.run_automa(
                "vehicles",
                "update",
                "autonomy",
                "--id",
                "piracer",
                "--skip-discovery",
                "--ssh-target",
                "piracer@example.local",
                "--dry-run",
                "--json",
                runtime_root=runtime_root,
            )

            payload = json.loads(result.stdout)
            self.assertEqual(payload["schema"], "vehicle_autonomy_update_v0")
            self.assertTrue(payload["dry_run"])
            self.assertEqual(payload["target"]["provider"], "picar")
            self.assertEqual(payload["activation"]["perception_algorithm"], "current")
            self.assertEqual(payload["activation"]["decision_engine"], "idle")
            self.assertTrue(payload["source"]["tree_sha256"])
            self.assertIn("controller-releases", payload["commands"][0]["command"])
            self.assertFalse(runtime_root.exists())

    def test_perception_plugin_enable_disable_edits_active_activation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            vehicle_id = "chase-sim-chaser"
            vehicle_runtime_dir = runtime_root / vehicle_id
            bundle = controller_bundle_paths(vehicle_runtime_dir)
            sync_controller_bundle(bundle, output=None)

            perception_dir = Path(bundle["root_dir"]) / "runtime" / "perception"
            self.write_json(
                perception_dir / "active.json",
                {
                    "schema": "automa_perception_activation_v0",
                    "vehicle_id": vehicle_id,
                    "vehicle_kind": "chase-sim-ws",
                    "provider": "chase-sim",
                    "controller_bundle": bundle,
                    "perception": {
                        "algorithm": "current",
                        "mapper_spec": CURRENT_MAPPER_SPEC,
                        "mapper_config": {
                            "plugins": ["frame"],
                            "plugin_specs": dict(PERCEPTION_PLUGIN_SPECS),
                        },
                        "source_dir": bundle["perception_dir"],
                    },
                },
            )

            enable = self.run_automa(
                "vehicles",
                "perception",
                "enable",
                "--id",
                vehicle_id,
                "floor_plane",
                "--json",
                runtime_root=runtime_root,
            )
            disable = self.run_automa(
                "vehicles",
                "perception",
                "disable",
                "--id",
                vehicle_id,
                "frame",
                "--json",
                runtime_root=runtime_root,
            )
            info = self.run_automa(
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

    @unittest.skipUnless(
        os.environ.get("AUTOMA_TEST_LIVE_SIM") == "1",
        "set AUTOMA_TEST_LIVE_SIM=1 to run live simulator integration",
    )
    def test_scenario_live_simulator_bounded_automation_smoke(self) -> None:
        run = self.run_automa(
            "vehicles",
            "automation",
            "run",
            "--id",
            "chase-sim-chaser",
            "--frames",
            "1",
            "--interval-s",
            "0",
            "--timeout-s",
            "6",
        )
        self.assertIn("Log: disabled", run.stdout)

        status = self.run_automa("vehicles", "automation", "status", "--id", "chase-sim-chaser", "--json")
        payload = json.loads(status.stdout)
        self.assertEqual(payload["vehicles"][0]["state"]["max_frames"], 1)

    def write_fake_deployment(
        self,
        runtime_root: Path,
        vehicle_id: str,
        *,
        pid: int,
        bundle_root: Path | None = None,
    ) -> None:
        vehicle_root = runtime_root / vehicle_id / "bundle"
        manifest_bundle_root = bundle_root or vehicle_root
        perception_dir = vehicle_root / "runtime" / "perception"
        decision_dir = vehicle_root / "runtime" / "decision"
        automation_dir = vehicle_root / "runtime" / "automation"
        perception_dir.mkdir(parents=True)
        decision_dir.mkdir(parents=True)
        automation_dir.mkdir(parents=True)

        self.write_json(
            perception_dir / "active.json",
            {
                "schema": "automa_perception_activation_v0",
                "perception": {
                    "algorithm": "current",
                    "mapper_spec": "autonomy.perception.mappers.current:CurrentDirectoryPerceptionMapper",
                    "mapper_config": {
                        "plugins": ["frame", "sim_color_targets"],
                        "plugin_specs": dict(PERCEPTION_PLUGIN_SPECS),
                    },
                },
                "controller_bundle": {
                    "root_dir": str(manifest_bundle_root),
                    "perception_source_dir": str(ROOT / "autonomy" / "perception"),
                },
            },
        )
        self.write_json(
            decision_dir / "active.json",
            {
                "schema": "automa_decision_activation_v0",
                "decision": {
                    "engine_id": "idle",
                    "engine_spec": "autonomy.runtime.engine:IdleAutonomyEngine",
                    "engine_config": {},
                    "engine_schema": {
                        "schema": "autonomy_engine_schema_v0",
                        "engine_id": "idle",
                    },
                },
                "controller_bundle": {
                    "root_dir": str(manifest_bundle_root),
                },
            },
        )
        self.write_json(
            automation_dir / "process.json",
            {
                "schema": "automa_automation_process_v0",
                "vehicle_id": vehicle_id,
                "pid": pid,
                "log_to_disk": False,
                "log_path": None,
                "command": [str(AUTOMA), "vehicles", "automation", "run", "--id", vehicle_id],
            },
        )
        self.write_json(
            automation_dir / "state.json",
            {
                "schema": "automa_automation_run_state_v0",
                "vehicle_id": vehicle_id,
                "run_id": "test-run",
                "status": "running",
                "pid": pid,
                "frames_processed": 3,
                "max_frames": None,
                "interval_s": 1.0,
                "recording": False,
                "control_source": "external_ws",
                "action_policy": "engine_idle",
                "last_frame": {
                    "frame_id": "frame_000002",
                    "things": 2,
                    "confidence": 0.75,
                    "perception_duration_ms": 12,
                    "cycle_duration_ms": 25,
                    "perception_completed_at_ms": 1000,
                },
            },
        )

    def write_json(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def make_fake_simeval(self, root: Path, mode: str) -> dict[str, str]:
        fake = root / "fake_simeval.py"
        state = root / "state.json"
        trace = root / "trace.jsonl"
        fake.write_text(
            """#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


mode = os.environ["FAKE_SIMEVAL_MODE"]
state_path = Path(os.environ["FAKE_SIMEVAL_STATE"])
trace_path = Path(os.environ["FAKE_SIMEVAL_TRACE"])
args = sys.argv[1:]

trace_path.parent.mkdir(parents=True, exist_ok=True)
with trace_path.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(args) + "\\n")


def read_state():
    if not state_path.exists():
        return {}
    return json.loads(state_path.read_text(encoding="utf-8"))


def write_state(payload):
    state_path.write_text(json.dumps(payload), encoding="utf-8")


def status_payload(online):
    return {
        "status": "success" if online else "failed",
        "local": {
            "deployments": [
                {
                    "key": "3000",
                    "server": "http://127.0.0.1:3000/api",
                    "workspace": "/tmp/fake-sim",
                    "overall": "running" if online else "stopped",
                    "processAlive": online,
                    "health": {"ok": online, "error": None if online else "fetch failed"},
                    "status": {"ok": online, "error": None if online else "Skipped status check (health failed)."},
                }
            ]
        },
    }


if args[:1] == ["status"]:
    state = read_state()
    online = mode == "online" or (mode == "offline_then_launch" and state.get("launched") is True)
    online = online or mode in ("online_no_frontend_then_open", "online_frontend_stale_until_open")
    print(json.dumps(status_payload(online)))
    raise SystemExit(0 if online else 1)

if args[:2] == ["ui", "verify"]:
    state = read_state()
    frontend_connected = mode in ("online", "offline_then_launch")
    frontend_connected = frontend_connected or bool(state.get("frontend_connected"))
    frontend_connected = frontend_connected or mode == "online_frontend_stale_until_open"
    print(json.dumps({
        "status": "success" if frontend_connected else "failed",
        "server": {
            "url": "http://127.0.0.1:5050",
            "autoServed": "--auto-serve" in args,
        },
        "samples": [
            {
                "frontendConnected": frontend_connected,
                "stateSource": "live",
            }
        ],
        "failures": [] if frontend_connected else [
            {
                "type": "frontend-not-connected",
                "message": "Frontend not connected.",
            }
        ],
    }))
    raise SystemExit(0)

if args[:2] == ["deploy", "start"]:
    if mode == "launch_fails":
        print("launch failed", file=sys.stderr)
        raise SystemExit(1)
    state = read_state()
    state["launched"] = True
    write_state(state)
    print(json.dumps({"status": "success", "action": "deploy-start"}))
    raise SystemExit(0)

if args[:2] == ["ui", "subapp"]:
    if mode == "ui_fails":
        print("ui unavailable", file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps({
        "status": "success",
        "action": "subapp",
        "app": "play",
        "uiUrl": "ws://127.0.0.1:5050/ws/control",
    }))
    raise SystemExit(0)

if args[:2] == ["ui", "play-game-action"]:
    if mode == "ui_fails":
        print("ui unavailable", file=sys.stderr)
        raise SystemExit(1)
    state = read_state()
    if mode == "online_frontend_stale_until_open" and not state.get("play_ready"):
        print("Timed out waiting for UI ack.", file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps({"status": "success", "action": "play-game-action"}))
    raise SystemExit(0)

if args[:2] == ["ui", "play-debug"]:
    state = read_state()
    frontend_connected = mode in ("online", "offline_then_launch")
    frontend_connected = frontend_connected or bool(state.get("frontend_connected"))
    if mode == "online_frontend_stale_until_open" and not state.get("play_ready"):
        print("Timed out waiting for Play debug.", file=sys.stderr)
        raise SystemExit(1)
    if not frontend_connected:
        print("Frontend not connected", file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps({"gameId": "chase", "frameIndex": 7}))
    raise SystemExit(0)

if args[:2] == ["deploy", "list"]:
    print(json.dumps({"deployments": []}))
    raise SystemExit(0)

print(f"unexpected fake simeval args: {args}", file=sys.stderr)
raise SystemExit(2)
""",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        return {
            "AUTOMA_SIMEVAL_BIN": str(fake),
            "FAKE_SIMEVAL_MODE": mode,
            "FAKE_SIMEVAL_STATE": str(state),
            "FAKE_SIMEVAL_TRACE": str(trace),
        }

    def make_fake_browser(self, root: Path) -> dict[str, str]:
        fake = root / "fake_browser.py"
        state = root / "state.json"
        trace = root / "browser_trace.txt"
        fake.write_text(
            """#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

state_path = Path(__file__).with_name("state.json")
trace_path = Path(__file__).with_name("browser_trace.txt")
url = sys.argv[-1]
state = {}
if state_path.exists():
    state = json.loads(state_path.read_text(encoding="utf-8"))
state["frontend_connected"] = True
state["play_ready"] = True
state_path.write_text(json.dumps(state), encoding="utf-8")
trace_path.write_text((trace_path.read_text(encoding="utf-8") if trace_path.exists() else "") + url + "\\n", encoding="utf-8")
print(f"opened {url}")
""",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        return {
            "AUTOMA_BROWSER_OPEN_COMMAND": str(fake),
            "FAKE_BROWSER_STATE": str(state),
            "FAKE_BROWSER_TRACE": str(trace),
        }

    def read_fake_simeval_calls(self, root: Path) -> list[list[str]]:
        trace = root / "trace.jsonl"
        if not trace.exists():
            return []
        return [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines() if line.strip()]

    def read_fake_browser_calls(self, root: Path) -> list[str]:
        trace = root / "browser_trace.txt"
        if not trace.exists():
            return []
        return [line for line in trace.read_text(encoding="utf-8").splitlines() if line.strip()]


if __name__ == "__main__":
    unittest.main(verbosity=2)
