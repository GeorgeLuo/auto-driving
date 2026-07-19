from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from cli.automa_cli.deploy import _resolve_physical_target
from implementations.perception.catalog import DEFAULT_PERCEPTION_ALGORITHM
from tests.support.cli_runner import run_automa


def _run_dry_run_forms(
    root: Path,
    component: str,
    *,
    restart: bool,
) -> tuple[
    subprocess.CompletedProcess[str],
    subprocess.CompletedProcess[str],
    dict,
]:
    options = [
        "--id",
        "piracer",
        "--skip-discovery",
        "--ssh-target",
        "piracer@example.local",
        "--dry-run",
    ]
    if restart:
        options.extend(["--restart", "--drive-args=--js"])

    human = run_automa(
        "vehicles",
        "update",
        component,
        *options,
        runtime_root=root / "runtime",
        extra_env={"PATH": ""},
    )
    machine = run_automa(
        "vehicles",
        "update",
        component,
        *options,
        "--json",
        runtime_root=root / "runtime",
        extra_env={"PATH": ""},
    )
    return human, machine, json.loads(machine.stdout)


class DeploymentUpdateTests(unittest.TestCase):
    def assert_dry_run_contract(
        self,
        *,
        root: Path,
        component: str,
        human: subprocess.CompletedProcess[str],
        machine: subprocess.CompletedProcess[str],
        payload: dict,
    ) -> None:
        self.assertEqual(payload["scope"]["id"], component)
        self.assertEqual(payload["vehicle_id"], "piracer")
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["target"]["provider"], "picar")
        self.assertEqual(payload["target"]["ssh_target"], "piracer@example.local")
        self.assertEqual(payload["target"]["pi_home"], "/home/piracer")
        self.assertEqual(
            payload["result"],
            {
                "local_writes_performed": False,
                "remote_connection_attempted": False,
                "remote_writes_performed": False,
                "status": "planned",
            },
        )
        self.assertGreaterEqual(len(payload["commands"]), 3)
        self.assertTrue(
            all(entry["status"] == "planned" for entry in payload["commands"])
        )

        self.assertIn(payload["scope"]["description"], human.stdout)
        self.assertIn(payload["target"]["provider"], human.stdout)
        self.assertIn(payload["target"]["ssh_target"], human.stdout)
        self.assertIn(payload["target"]["pi_home"], human.stdout)
        self.assertIn(
            "outcome: plan only; no files written and no remote connection attempted",
            human.stdout,
        )
        for entry in payload["commands"]:
            self.assertIn(entry["step"], human.stdout)
            self.assertIn(entry["command"], human.stdout)

        self.assertEqual(human.stderr, "")
        self.assertEqual(machine.stderr, "")
        self.assertFalse((root / "runtime").exists())

    def test_core_dry_run_has_matching_human_and_json_plan_without_side_effects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            human, machine, payload = _run_dry_run_forms(root, "core", restart=True)
            self.assert_dry_run_contract(
                root=root,
                component="core",
                human=human,
                machine=machine,
                payload=payload,
            )

        self.assertEqual(payload["schema"], "vehicle_core_update_v0")
        self.assertEqual(
            payload["scope"]["excluded"],
            ["autonomy", "implementations", "runtime"],
        )
        self.assertEqual(
            payload["source"]["vendor"]["checkout"],
            "deploy/targets/donkeycar/vendor/donkeycar",
        )
        self.assertEqual(payload["source"]["harness"], "deploy/targets/donkeycar/app")
        self.assertEqual(payload["source"]["service"]["name"], "automa-donkey.service")
        self.assertEqual(
            payload["source"]["service"]["source"],
            "deploy/targets/donkeycar/systemd",
        )
        self.assertTrue(payload["source"]["service"]["boot_enabled"])
        self.assertIsNone(payload["runtime_readiness"])
        self.assertTrue(payload["restart_requested"])
        self.assertEqual(payload["commands"][-1]["step"], "Restart Donkey runtime service")
        self.assertIn("systemd/control.sh", payload["commands"][-1]["command"])
        self.assertIn("b64:LS1qcw==", payload["commands"][-1]["command"])
        self.assertIn(
            "Install and enable Donkey runtime service",
            [entry["step"] for entry in payload["commands"]],
        )
        self.assertIn("--exclude=autonomy", payload["commands"][2]["command"])
        self.assertIn("automa-donkey.service", human.stdout)
        self.assertIn("enabled at boot", human.stdout)
        self.assertIn("restart requested: yes", human.stdout)

    def test_autonomy_dry_run_has_matching_human_and_json_plan_without_side_effects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            human, machine, payload = _run_dry_run_forms(root, "autonomy", restart=True)
            self.assert_dry_run_contract(
                root=root,
                component="autonomy",
                human=human,
                machine=machine,
                payload=payload,
            )

        self.assertEqual(payload["schema"], "vehicle_autonomy_update_v0")
        self.assertTrue(payload["release_id"].endswith("-preview"))
        self.assertTrue(payload["source"]["tree_sha256"])
        self.assertEqual(
            payload["activation"]["perception_algorithm"],
            DEFAULT_PERCEPTION_ALGORITHM,
        )
        self.assertEqual(payload["activation"]["decision_engine"], "idle")
        self.assertEqual(payload["activation"]["memory_implementation"], "bounded_evidence")
        self.assertTrue(payload["restart_requested"])
        self.assertEqual(payload["commands"][-1]["step"], "Restart Donkey runtime service")
        self.assertIn("systemd/control.sh", payload["commands"][-1]["command"])
        self.assertIn("restart requested: yes", human.stdout)

    def test_restart_is_absent_from_both_plans_when_not_requested(self) -> None:
        for component in ("core", "autonomy"):
            with self.subTest(component=component), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                human, machine, payload = _run_dry_run_forms(
                    root,
                    component,
                    restart=False,
                )
                self.assert_dry_run_contract(
                    root=root,
                    component=component,
                    human=human,
                    machine=machine,
                    payload=payload,
                )

                self.assertFalse(payload["restart_requested"])
                self.assertNotIn(
                    "Restart Donkey runtime service",
                    [entry["step"] for entry in payload["commands"]],
                )
                self.assertIn("restart requested: no", human.stdout)

    def test_drive_arguments_require_an_explicit_restart(self) -> None:
        for component in ("core", "autonomy"):
            with self.subTest(component=component), tempfile.TemporaryDirectory() as tmp:
                result = run_automa(
                    "vehicles",
                    "update",
                    component,
                    "--id",
                    "piracer",
                    "--skip-discovery",
                    "--ssh-target",
                    "piracer@example.local",
                    "--dry-run",
                    "--drive-args=--js",
                    runtime_root=Path(tmp) / "runtime",
                    extra_env={"PATH": ""},
                    check=False,
                )

                self.assertEqual(result.returncode, 2)
                self.assertIn("--drive-args requires --restart", result.stdout)
                self.assertEqual(result.stderr, "")

    def test_core_can_bootstrap_the_configured_picar_when_http_is_down(self) -> None:
        output = StringIO()
        with patch(
            "cli.automa_cli.deploy.discover_active_vehicles",
            return_value={"vehicles": []},
        ) as discover:
            target, error = _resolve_physical_target(
                vehicle_id="piracer",
                timeout_s=0.1,
                ssh_target=None,
                pi_home=None,
                skip_discovery=False,
                output=output,
                operation="core deploy",
                allow_offline_default=True,
            )

        self.assertIsNone(error)
        self.assertIsNotNone(target)
        assert target is not None
        self.assertEqual(target.ssh_target, "piracer@piracer.local")
        discover.assert_called_once_with(
            timeout_s=0.1,
            include_picar=True,
            include_chase_sim=False,
        )
        self.assertIn("HTTP readiness is unavailable", output.getvalue())
        self.assertIn("SSH will determine deploy reachability", output.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
