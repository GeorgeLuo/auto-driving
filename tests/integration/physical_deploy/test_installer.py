from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from autonomy.decision import read_memory_activation
from autonomy.runtime import read_decision_activation
from cli.automa_cli.bundles import controller_bundle_paths, sync_controller_bundle
from cli.automa_cli.decision import ensure_vehicle_decision_activation
from cli.automa_cli.deploy import (
    PhysicalTarget,
    _REMOTE_AUTONOMY_INSTALL_SCRIPT,
    _verify_physical_autonomy_runtime,
    _write_remote_activation_files,
)
from cli.automa_cli.memory import ensure_vehicle_memory_activation
from cli.automa_cli.perception import ensure_vehicle_perception_activation
from implementations.perception.catalog import PERCEPTION_ALGORITHMS


class PhysicalDeployTests(unittest.TestCase):
    def test_named_perception_activation_is_refreshed_from_current_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vehicle_runtime = Path(tmp) / "runtime" / "vehicles" / "piracer"
            bundle = controller_bundle_paths(vehicle_runtime)
            release = sync_controller_bundle(bundle, output=None)
            vehicle = {
                "vehicle_id": "piracer",
                "vehicle_kind": "picar",
                "provider": "picar",
                "connection": {"base_url": "http://piracer.local:8887"},
            }
            activation_path = ensure_vehicle_perception_activation(
                vehicle=vehicle,
                algorithm="visual_observer",
                bundle=bundle,
                release=release,
            )
            stale = json.loads(activation_path.read_text(encoding="utf-8"))
            stale["perception"]["mapper_spec"] = (
                "autonomy.perception.mappers.current:CurrentDirectoryPerceptionMapper"
            )
            stale["perception"]["mapper_config"] = {"plugins": ["stale"]}
            activation_path.write_text(json.dumps(stale), encoding="utf-8")

            refreshed_path = ensure_vehicle_perception_activation(
                vehicle=vehicle,
                algorithm="lightweight_observer",
                bundle=bundle,
                release=release,
            )
            refreshed = json.loads(refreshed_path.read_text(encoding="utf-8"))

        self.assertEqual(refreshed["perception"]["algorithm"], "visual_observer")
        self.assertEqual(
            refreshed["perception"]["mapper_spec"],
            PERCEPTION_ALGORITHMS["visual_observer"]["mapper_spec"],
        )
        self.assertEqual(
            refreshed["perception"]["mapper_config"],
            PERCEPTION_ALGORITHMS["visual_observer"]["mapper_config"],
        )

    def test_runtime_verification_requires_selected_engine_and_manual_mode(self) -> None:
        target = PhysicalTarget(
            vehicle_id="piracer",
            vehicle={
                "provider": "picar",
                "connection": {"base_url": "http://piracer.local:8887"},
            },
            provider="picar",
            ssh_target="piracer@piracer.local",
            pi_home="/home/piracer",
        )
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps(
            {
                "ok": True,
                "drive_mode": "user",
                "autonomy": {
                    "engine": "autonomy.runtime.engine:IdleAutonomyEngine",
                    "components": {"perception": {"algorithm": "lightweight_observer"}},
                },
            }
        ).encode("utf-8")

        with patch("cli.automa_cli.deploy.urllib_request.urlopen", return_value=response):
            verification = _verify_physical_autonomy_runtime(
                target=target,
                expected_engine_spec="autonomy.runtime.engine:IdleAutonomyEngine",
                expected_perception_algorithm="lightweight_observer",
                timeout_s=3.0,
            )

        self.assertTrue(verification["ok"])
        self.assertEqual(verification["drive_mode"], "user")

        response.read.return_value = json.dumps(
            {
                "ok": True,
                "drive_mode": "local",
                "autonomy": {
                    "engine": "autonomy.runtime.engine:IdleAutonomyEngine",
                    "components": {"perception": {"algorithm": "lightweight_observer"}},
                },
            }
        ).encode("utf-8")
        with patch("cli.automa_cli.deploy.urllib_request.urlopen", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "expected 'user'"):
                _verify_physical_autonomy_runtime(
                    target=target,
                    expected_engine_spec="autonomy.runtime.engine:IdleAutonomyEngine",
                    expected_perception_algorithm="lightweight_observer",
                    timeout_s=3.0,
                )

    def test_remote_installer_verifies_and_activates_packaged_release(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vehicle_runtime = root / "runtime" / "vehicles" / "piracer"
            bundle = controller_bundle_paths(vehicle_runtime)
            release = sync_controller_bundle(bundle, output=None)
            target = PhysicalTarget(
                vehicle_id="piracer",
                vehicle={
                    "vehicle_id": "piracer",
                    "vehicle_kind": "picar",
                    "provider": "picar",
                    "connection": {"base_url": "http://piracer.local:8887"},
                },
                provider="picar",
                ssh_target="piracer@piracer.local",
                pi_home="/home/piracer",
            )
            perception_activation = ensure_vehicle_perception_activation(
                vehicle=dict(target.vehicle),
                algorithm="lightweight_observer",
                bundle=bundle,
                release=release,
            )
            decision_activation = ensure_vehicle_decision_activation(
                vehicle_id="piracer",
                bundle=bundle,
                release=release,
            )
            memory_activation = ensure_vehicle_memory_activation(
                vehicle_id="piracer",
                bundle=bundle,
                release=release,
            )
            release_id = Path(release["archive"]["path"]).name.removesuffix(".tar.gz")
            deploy_files = _write_remote_activation_files(
                target=target,
                vehicle_runtime_dir=vehicle_runtime,
                release=release,
                release_id=release_id,
                perception_activation_path=perception_activation,
                decision_activation_path=decision_activation,
                memory_activation_path=memory_activation,
            )

            app_root = root / "remote" / "mycar"
            release_root = app_root / "runtime" / "controller-releases" / release_id
            app_root.mkdir(parents=True)
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    _REMOTE_AUTONOMY_INSTALL_SCRIPT,
                    str(release["archive"]["path"]),
                    str(release_root),
                    str(app_root),
                    str(release["archive"]["sha256"]),
                    str(deploy_files["perception"]),
                    str(deploy_files["decision"]),
                    str(deploy_files["memory"]),
                    release_id,
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((app_root / "autonomy").is_symlink())
            self.assertTrue((app_root / "implementations").is_symlink())
            self.assertEqual((app_root / "autonomy").resolve(), (release_root / "autonomy").resolve())
            activation = read_decision_activation(app_root / "runtime" / "decision" / "active.json")
            self.assertEqual(activation.engine_id, "idle")
            memory = read_memory_activation(app_root / "runtime" / "memory" / "active.json")
            self.assertEqual(memory.implementation_id, "bounded_evidence")
            installed = json.loads(
                (app_root / "runtime" / "controller-release.json").read_text(encoding="utf-8")
            )
            self.assertEqual(installed["release_id"], release_id)
            self.assertEqual(installed["archive_sha256"], release["archive"]["sha256"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
