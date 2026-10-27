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
from cli.automa_cli.step_activations import CONTROLLER_BUNDLE_KEYS
from implementations.decision_cycle.catalog import preset_activation
from tests.shared.cli.invoke import invoke_automa
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
        "controller_bundle": {
            key: bundle[key] for key in CONTROLLER_BUNDLE_KEYS if key in bundle
        },
        **metadata,
    }
    return payload


class PerceptionCommandTests(unittest.TestCase):
    def test_perception_bundle_syncs_configured_visual_observer_plugins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            host_name = "chase-sim-chaser"
            host_dir = runtime_root / host_name
            bundle = controller_bundle_paths(host_dir)
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
                    host_name=host_name,
                    vehicle_kind="chase-sim-ws",
                    provider="chase-sim",
                ),
            )

            json_result = invoke_automa(
                "hosts", "info",
                "perception",
                "--name",
                host_name,
                "--json",
                runtime_root=runtime_root,
            )
            text_result = invoke_automa(
                "hosts", "info",
                "perception",
                "--name",
                host_name,
                runtime_root=runtime_root,
            )

        payload = json.loads(json_result.stdout)
        self.assertEqual(payload["activation"]["preset"], "visual_observer")
        self.assertEqual(
            payload["controller_bundle"]["release"]["tree_sha256"],
            release_manifest["tree_sha256"],
        )
        self.assertEqual(set(payload["controller_bundle"]), set(CONTROLLER_BUNDLE_KEYS))
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
            "  contract: stateless feeds=camera.rgb:front_camera",
            text_result.stdout,
        )

    def test_physical_perception_staging_reuses_local_metadata_while_offline(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            from tests.support.runtime_fixtures import write_host_fixture
            write_host_fixture(runtime_root, "piracer", provider="picar")
            bundle = controller_bundle_paths(runtime_root / "piracer")
            sync_controller_bundle(bundle, output=None)
            write_json(
                Path(bundle["perception_runtime_dir"]) / "active.json",
                _activation(
                    bundle,
                    host_name="piracer",
                    vehicle_kind="picar",
                    provider="picar",
                    runtime={"kind": "onboard_controller", "connection": {}},
                ),
            )

            result = invoke_automa(
                "hosts", "stage",
                "perception",
                "--name",
                "piracer",
                "--preset",
                "visual_observer",
                "--json",
                runtime_root=runtime_root,
            )

        payload = json.loads(result.stdout)
        self.assertEqual(payload["host_name"], "piracer")
        self.assertEqual(payload["preset"], "visual_observer")
        self.assertEqual(payload["manifest"]["metadata"]["provider"], "picar")


if __name__ == "__main__":
    unittest.main()
