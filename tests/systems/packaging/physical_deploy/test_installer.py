from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from autonomy.decision_cycle.activation import STEPS, read_step_activation
from cli.automa_cli.hosts.bundles import controller_bundle_paths, sync_controller_bundle
from cli.automa_cli.hosts.deploy import PicarTarget, _REMOTE_AUTONOMY_INSTALL_SCRIPT, _write_remote_activation_files
from cli.automa_cli.hosts.step_activations import (
    CONTROLLER_BUNDLE_KEYS,
    bundle_activation_path,
    ensure_step_activations,
    startup_activations,
)

TARGET = PicarTarget(
    host_name="piracer",
    configuration={
        "host_name": "piracer",
        "vehicle_kind": "picar",
        "provider": "picar",
        "connection": {"base_url": "http://piracer.local:8887"},
    },
    provider="picar",
    ssh_target="piracer@piracer.local",
    pi_home="/home/piracer",
)


class PhysicalDeployTests(unittest.TestCase):


    def test_remote_installer_verifies_and_activates_packaged_release(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host_dir = root / "runtime" / "hosts" / "piracer"
            bundle = controller_bundle_paths(host_dir)
            release = sync_controller_bundle(bundle, output=None)
            ensure_step_activations(
                host_name="piracer", bundle=bundle, release=release,
                activations=startup_activations(bundle, "piracer"), vehicle=dict(TARGET.configuration),
            )
            steps = STEPS
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
                host_dir=host_dir,
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
            proposal = read_step_activation(runtime / "proposal" / "active.json", "proposal")
            self.assertEqual(proposal.metadata["preset"], "obstruction_avoidance")
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
