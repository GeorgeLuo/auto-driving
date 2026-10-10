from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from autonomy.decision_cycle.activation import read_step_activation
from cli.automa_cli.bundles import controller_bundle_paths, sync_controller_bundle
from cli.automa_cli.deploy import (
    PicarTarget,
    _REMOTE_AUTONOMY_INSTALL_SCRIPT,
    _verify_picar_autonomy_runtime,
    _write_remote_activation_files,
)
from cli.automa_cli.memory import ensure_vehicle_memory_activation
from cli.automa_cli.perception import ensure_vehicle_perception_activation
from cli.automa_cli.step_activations import (
    CONTROLLER_BUNDLE_KEYS,
    bundle_activation_path,
    ensure_builtin_activations,
)
from implementations.decision_cycle.perception.presets import PERCEPTION_PRESETS

TARGET = PicarTarget(
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


def _status_response(mode: str, steps: dict[str, list[str]]) -> MagicMock:
    response = MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = json.dumps(
        {
            "ok": True,
            "mode": mode,
            "autonomy": {
                "steps": {step: {"plugin_ids": plugins} for step, plugins in steps.items()},
                "components": {},
            },
        }
    ).encode("utf-8")
    return response


class PhysicalDeployTests(unittest.TestCase):
    def test_named_perception_activation_is_refreshed_from_current_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = controller_bundle_paths(Path(tmp) / "runtime" / "vehicles" / "piracer")
            release = sync_controller_bundle(bundle, output=None)
            activation_path = ensure_vehicle_perception_activation(
                vehicle=dict(TARGET.vehicle),
                preset="visual_observer",
                bundle=bundle,
                release=release,
            )
            stale = json.loads(activation_path.read_text(encoding="utf-8"))
            stale["plugins"] = ["frame"]
            activation_path.write_text(json.dumps(stale), encoding="utf-8")

            refreshed_path = ensure_vehicle_perception_activation(
                vehicle=dict(TARGET.vehicle),
                preset="lightweight_observer",
                bundle=bundle,
                release=release,
            )
            refreshed = json.loads(refreshed_path.read_text(encoding="utf-8"))

        self.assertEqual(refreshed["metadata"]["preset"], "visual_observer")
        self.assertEqual(refreshed["plugins"], PERCEPTION_PRESETS["visual_observer"]["plugins"])

    def test_runtime_verification_requires_every_deployed_step_and_manual_mode(self) -> None:
        expected = {"memory": ["bounded_evidence"], "action": ["hold"]}
        cases = (
            ("manual", expected, None),
            ("autonomy", expected, "expected 'manual'"),
            ("manual", {"action": ["hold"]}, "no live memory step"),
            ("manual", {**expected, "action": ["mode"]}, "expected \\['hold'\\]"),
        )
        for mode, reported, error in cases:
            with self.subTest(mode=mode, reported=reported), patch(
                "cli.automa_cli.deploy.urllib_request.urlopen",
                return_value=_status_response(mode, reported),
            ):
                if error is None:
                    verification = _verify_picar_autonomy_runtime(
                        target=TARGET, expected_steps=expected, timeout_s=3.0
                    )
                    self.assertEqual(verification["mode"], "manual")
                    continue
                with self.assertRaisesRegex(RuntimeError, error):
                    _verify_picar_autonomy_runtime(
                        target=TARGET, expected_steps=expected, timeout_s=3.0
                    )

    def test_remote_installer_verifies_and_activates_packaged_release(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vehicle_runtime = root / "runtime" / "vehicles" / "piracer"
            bundle = controller_bundle_paths(vehicle_runtime)
            release = sync_controller_bundle(bundle, output=None)
            ensure_vehicle_perception_activation(
                vehicle=dict(TARGET.vehicle),
                preset="lightweight_observer",
                bundle=bundle,
                release=release,
            )
            ensure_vehicle_memory_activation(vehicle_id="piracer", bundle=bundle, release=release)
            ensure_builtin_activations(vehicle_id="piracer", bundle=bundle, release=release)
            steps = ("perception", "memory", "observation", "plan", "action")
            release_id = Path(release["archive"]["path"]).name.removesuffix(".tar.gz")
            for step in steps:
                staged = json.loads(bundle_activation_path(bundle, step).read_text(encoding="utf-8"))
                self.assertEqual(
                    set(staged["metadata"]["controller_bundle"]),
                    set(CONTROLLER_BUNDLE_KEYS),
                )
                self.assertNotIn("source_dir", staged["metadata"])
            deploy_files = _write_remote_activation_files(
                target=TARGET,
                vehicle_runtime_dir=vehicle_runtime,
                release=release,
                release_id=release_id,
                activation_paths={step: bundle_activation_path(bundle, step) for step in steps},
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
                    release_id,
                    *(str(path) for path in deploy_files.values()),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            for step in steps:
                remote = json.loads(deploy_files[step].read_text(encoding="utf-8"))
                self.assertEqual(
                    set(remote["metadata"]["controller_bundle"]),
                    set(CONTROLLER_BUNDLE_KEYS),
                )
                self.assertNotIn("source_dir", remote["metadata"])
                self.assertTrue(
                    str(remote["metadata"]["controller_bundle"]["autonomy_dir"]).endswith("/autonomy")
                )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((app_root / "autonomy").is_symlink())
            self.assertTrue((app_root / "implementations").is_symlink())
            self.assertEqual((app_root / "autonomy").resolve(), (release_root / "autonomy").resolve())
            runtime = app_root / "runtime"
            action = read_step_activation(runtime / "action" / "active.json", "action")
            self.assertEqual(action.plugins, ("selected",))
            memory = read_step_activation(runtime / "memory" / "active.json", "memory")
            self.assertEqual(memory.plugins, ("bounded_evidence",))
            self.assertEqual(
                json.loads((runtime / "identity.json").read_text(encoding="utf-8"))["source_id"],
                "donkeycar:piracer",
            )
            installed = json.loads(
                (runtime / "controller-release.json").read_text(encoding="utf-8")
            )
            self.assertEqual(installed["release_id"], release_id)
            self.assertEqual(installed["archive_sha256"], release["archive"]["sha256"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
