from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

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
        self.assertTrue(payload["restart_requested"])
        self.assertEqual(payload["commands"][-1]["step"], "Restart Donkey drive server")
        self.assertIn("PI_HOME=/home/piracer", payload["commands"][-1]["command"])
        self.assertIn("DRIVE_ARGS=--js", payload["commands"][-1]["command"])
        self.assertIn("--exclude=autonomy", payload["commands"][2]["command"])
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
        self.assertTrue(payload["restart_requested"])
        self.assertEqual(payload["commands"][-1]["step"], "Restart Donkey drive server")
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
                    "Restart Donkey drive server",
                    [entry["step"] for entry in payload["commands"]],
                )
                self.assertIn("restart requested: no", human.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
