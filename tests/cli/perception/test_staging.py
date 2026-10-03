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
from implementations.decision_cycle.catalog import CUSTOM_PRESET, preset_activation
from implementations.decision_cycle.perception.presets import (
    CUSTOM_PERCEPTION_DESCRIPTION,
    DEFAULT_PERCEPTION_PRESET,
)
from tests.support.cli_runner import run_automa
from tests.support.runtime_fixtures import write_json


def _activation(
    bundle: dict,
    preset: str = "lightweight_observer",
    *,
    plugins: list[str] | None = None,
    **metadata,
) -> dict:
    """A staged perception activation for ``preset`` (optionally reselected)."""

    payload = preset_activation("perception", preset).to_payload()
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
        self.assertEqual(payload["activation"]["preset"], "visual_observer")
        self.assertEqual(
            payload["controller_bundle"]["release"]["tree_sha256"],
            release_manifest["tree_sha256"],
        )
        self.assertEqual(
            payload["activation"]["plugins"],
            ["frame", "floor_plane", "motion_tracks"],
        )
        chain = payload["perception_schema"]["plugins"]
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
            "frame [stateless] feeds=camera.rgb:front_camera",
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
        self.assertEqual(payload["preset"], DEFAULT_PERCEPTION_PRESET)
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
                "--preset",
                "visual_observer",
                "--json",
                runtime_root=runtime_root,
            )

        payload = json.loads(result.stdout)
        self.assertEqual(payload["vehicle_id"], "piracer")
        self.assertEqual(payload["preset"], "visual_observer")
        self.assertEqual(payload["manifest"]["metadata"]["provider"], "picar")

    def test_perception_update_plugins_stages_a_custom_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            result = run_automa(
                "vehicles",
                "update",
                "perception",
                "--id",
                "chase-sim-chaser",
                "--plugin",
                "frame",
                "--plugin",
                "classical_regions",
                "--dry-run",
                "--json",
                runtime_root=runtime_root,
            )

        payload = json.loads(result.stdout)
        self.assertEqual(payload["preset"], CUSTOM_PRESET)
        self.assertEqual(payload["manifest"]["plugins"], ["frame", "classical_regions"])
        self.assertEqual(
            payload["manifest"]["metadata"]["preset_description"],
            CUSTOM_PERCEPTION_DESCRIPTION,
        )

    def test_perception_update_rejects_a_preset_with_plugins_and_unknown_plugins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            both = run_automa(
                "vehicles",
                "update",
                "perception",
                "--id",
                "chase-sim-chaser",
                "--preset",
                "visual_observer",
                "--plugin",
                "frame",
                "--dry-run",
                runtime_root=runtime_root,
                check=False,
            )
            unknown = run_automa(
                "vehicles",
                "update",
                "perception",
                "--id",
                "chase-sim-chaser",
                "--plugin",
                "no_such_plugin",
                "--dry-run",
                runtime_root=runtime_root,
                check=False,
            )

        self.assertNotEqual(both.returncode, 0)
        self.assertNotEqual(unknown.returncode, 0)
        self.assertIn("no_such_plugin", unknown.stdout + unknown.stderr)
