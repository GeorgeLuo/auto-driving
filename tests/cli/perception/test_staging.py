from __future__ import annotations
import json
import tempfile
import unittest
from pathlib import Path
from cli.automa_cli.bundles import (
    controller_bundle_paths,
    release_activation_summary,
    sync_controller_bundle,
)
from implementations.decision_cycle.catalog import perception_algorithm_activation
from implementations.decision_cycle.perception.catalog import DEFAULT_PERCEPTION_ALGORITHM
from tests.support.cli_runner import run_automa
from tests.support.runtime_fixtures import write_json


def _activation(
    bundle: dict,
    algorithm: str = "lightweight_observer",
    *,
    plugins: list[str] | None = None,
    **metadata,
) -> dict:
    """A staged perception activation for ``algorithm`` (optionally reselected)."""

    payload = perception_algorithm_activation(algorithm).to_payload()
    if plugins is not None:
        payload["plugins"] = plugins
    payload["metadata"] = {
        **payload["metadata"],
        "controller_bundle": bundle,
        "source_dir": bundle["perception_dir"],
        **metadata,
    }
    return payload


class PerceptionCommandTests(unittest.TestCase):
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
            self.assertEqual(
                release_manifest["archive"]["sha256"], release["archive"]["sha256"]
            )
            self.assertEqual(release_manifest["file_count"], release["file_count"])
            self.assertEqual(
                [source["package_root"] for source in release_manifest["sources"]],
                ["autonomy", "implementations"],
            )

            for relative in (
                "implementations/decision_cycle/perception/plugins/floor_plane/plugin.py",
                "implementations/decision_cycle/perception/plugins/vlm_prep/plugin.py",
                "implementations/decision_cycle/perception/plugins/motion_tracks/plugin.py",
                "autonomy/decision_cycle/perception/runner.py",
                "bundle-manifest.json",
            ):
                self.assertTrue((bundle_root / relative).exists(), relative)

            write_json(
                bundle_root / "runtime" / "perception" / "active.json",
                _activation(
                    {**bundle, "release": release_activation_summary(release)},
                    "visual_observer",
                    vehicle_id=vehicle_id,
                    vehicle_kind="chase-sim-ws",
                    provider="chase-sim",
                ),
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
        self.assertEqual(
            payload["controller_bundle"]["release"]["tree_sha256"],
            release_manifest["tree_sha256"],
        )
        self.assertEqual(
            payload["activation"]["plugins"],
            ["frame", "floor_plane", "motion_tracks"],
        )
        chain = payload["algorithm_schema"]["plugins"]
        self.assertEqual(
            [plugin["plugin_id"] for plugin in chain],
            [
                "frame",
                "floor_plane",
                "motion_tracks",
            ],
        )
        self.assertEqual(
            [plugin["plugin_id"] for plugin in chain],
            [
                "frame",
                "floor_plane",
                "motion_tracks",
            ],
        )
        self.assertIn(
            "Enabled plugins: frame, floor_plane, motion_tracks", text_result.stdout
        )
        self.assertIn("Plugins:", text_result.stdout)
        self.assertIn(
            "frame [stateless] components=camera.rgb:front_camera",
            text_result.stdout,
        )

    def test_perception_update_dry_run_json_does_not_require_live_simulator(
        self,
    ) -> None:
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
        self.assertEqual(payload["manifest"]["metadata"]["provider"], "chase-sim")
        self.assertTrue(
            payload["would_write"]["bundle_root"].endswith(
                "vehicles/chase-sim-chaser/bundle"
            )
        )

    def test_physical_perception_staging_reuses_local_metadata_while_offline(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            bundle = controller_bundle_paths(runtime_root / "piracer")
            sync_controller_bundle(bundle, output=None)
            write_json(
                Path(bundle["perception_runtime_dir"]) / "active.json",
                _activation(
                    bundle,
                    vehicle_id="piracer",
                    vehicle_kind="picar",
                    provider="picar",
                    runtime={"kind": "onboard_controller", "connection": {}},
                ),
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
        self.assertEqual(payload["manifest"]["metadata"]["provider"], "picar")

    def test_perception_plugin_enable_disable_edits_active_activation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            vehicle_id = "chase-sim-chaser"
            vehicle_runtime_dir = runtime_root / vehicle_id
            bundle = controller_bundle_paths(vehicle_runtime_dir)
            sync_controller_bundle(bundle, output=None)

            write_json(
                Path(bundle["root_dir"]) / "runtime" / "perception" / "active.json",
                _activation(
                    bundle,
                    plugins=["frame"],
                    vehicle_id=vehicle_id,
                    vehicle_kind="chase-sim-ws",
                    provider="chase-sim",
                ),
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
        self.assertEqual(
            info_payload["activation"]["plugins"], ["floor_plane"]
        )
