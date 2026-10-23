from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from autonomy.decision_cycle.activation import DECISION_STEPS, STEPS
from tests.shared.cli.invoke import invoke_automa
from tests.support.fake_metrics_ui import fake_metrics_ui_server
from tests.support.runtime_fixtures import write_runtime_fixture


VEHICLE_ID = "chase-sim-chaser"


class StagedActivationRecoveryTests(unittest.TestCase):
    def test_every_invalid_step_has_the_same_human_and_json_recovery_without_launching(self) -> None:
        with fake_metrics_ui_server() as ws_url:
            for step in STEPS:
                with self.subTest(step=step), tempfile.TemporaryDirectory() as tmp:
                    runtime_root = Path(tmp) / "vehicles"
                    paths = write_runtime_fixture(runtime_root, VEHICLE_ID, pid=999_999_999)
                    paths.automation_process.unlink()
                    paths.automation_state.unlink()
                    path = paths.bundle_root / "runtime" / step / "active.json"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    original = json.dumps({"schema": f"automa_{step}_activation_v0"})
                    path.write_text(original, encoding="utf-8")
                    env = {"CHASE_UI_WS_URL": ws_url}
                    command = f"./cli/automa vehicles update {step} --id {VEHICLE_ID}"
                    human = invoke_automa(
                        "vehicles", "automation", "status", "--id", VEHICLE_ID,
                        runtime_root=runtime_root, extra_env=env,
                    )
                    self.assertIn(f"Invalid {step} activation:", human.stdout)
                    self.assertIn(f"next: {command}", human.stdout)
                    automation = json.loads(invoke_automa(
                        "vehicles", "automation", "status", "--id", VEHICLE_ID, "--json",
                        runtime_root=runtime_root, extra_env=env,
                    ).stdout)
                    self.assertEqual(automation["outcome"]["status"], "degraded")
                    self.assertEqual(automation["vehicles"][0]["activation_problems"][0]["step"], step)
                    self.assertEqual(automation["vehicles"][0]["process"]["recovery"], command)
                    for verb in ("run", "restart"):
                        result = invoke_automa(
                            "vehicles", "automation", verb, "--id", VEHICLE_ID,
                            "--observe-only", "--num-decisions", "0", "--open-view",
                            runtime_root=runtime_root, extra_env=env, check=False,
                        )
                        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                        self.assertIn(command, result.stdout)
                        self.assertEqual(result.stderr, "")
                        self.assertFalse(paths.automation_process.exists())
                        self.assertFalse(paths.automation_state.exists())
                    self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_staging_one_step_leaves_old_memory_for_status_and_explicit_restage_recovers(self) -> None:
        """Regression: staging another step must not replace or hide an invalid memory document."""

        with tempfile.TemporaryDirectory() as tmp, fake_metrics_ui_server() as ws_url:
            runtime_root = Path(tmp) / "vehicles"
            path = runtime_root / VEHICLE_ID / "bundle/runtime/memory/active.json"
            path.parent.mkdir(parents=True)
            original = json.dumps({"schema": "automa_memory_activation_v0"})
            path.write_text(original, encoding="utf-8")
            env = {"CHASE_UI_WS_URL": ws_url}
            invoke_automa(
                "vehicles", "update", "perception", "--id", VEHICLE_ID,
                "--preset", "lightweight_observer", "--json",
                runtime_root=runtime_root, extra_env=env,
            )
            self.assertEqual(path.read_text(encoding="utf-8"), original)
            automation = json.loads(invoke_automa(
                "vehicles", "automation", "status", "--id", VEHICLE_ID, "--json",
                runtime_root=runtime_root, extra_env=env,
            ).stdout)
            self.assertEqual(automation["outcome"]["status"], "degraded")
            self.assertEqual(automation["vehicles"][0]["activation_problems"][0]["step"], "memory")
            invoke_automa(
                "vehicles", "update", "memory", "--id", VEHICLE_ID,
                "--preset", "recency_ledger", runtime_root=runtime_root, extra_env=env,
            )
            restaged = json.loads(invoke_automa(
                "vehicles", "automation", "status", "--id", VEHICLE_ID, "--json",
                runtime_root=runtime_root, extra_env=env,
            ).stdout)["vehicles"][0]
            self.assertTrue(restaged["deployed"])
            self.assertFalse(restaged["process"]["running"])

    def test_info_and_stream_name_the_invalid_step_and_its_restage_command(self) -> None:
        with fake_metrics_ui_server() as ws_url:
            for step in ("perception", "memory", *DECISION_STEPS):
                with self.subTest(step=step), tempfile.TemporaryDirectory() as tmp:
                    runtime_root = Path(tmp) / "vehicles"
                    path = runtime_root / VEHICLE_ID / "bundle/runtime" / step / "active.json"
                    path.parent.mkdir(parents=True)
                    path.write_text(json.dumps({"schema": f"automa_{step}_activation_v0"}), encoding="utf-8")
                    env = {"CHASE_UI_WS_URL": ws_url}
                    command = f"./cli/automa vehicles update {step} --id {VEHICLE_ID}"
                    # Each step's info reads its own activation; a decision step's stream reads all three.
                    surfaces = [("info", step)]
                    if step in DECISION_STEPS:
                        surfaces.append(("stream", step, "--once"))
                    for surface in surfaces:
                        result = invoke_automa(
                            "vehicles", *surface, "--id", VEHICLE_ID,
                            runtime_root=runtime_root, extra_env=env, check=False,
                        )
                        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                        self.assertEqual(result.stderr, "")
                        self.assertIn(f"Invalid {step} activation:", result.stdout)
                        self.assertIn(f"Restage with your intended selection: {command}", result.stdout)
                    if step not in DECISION_STEPS:
                        continue
                    machine = invoke_automa(
                        "vehicles", "stream", step, "--once", "--id", VEHICLE_ID, "--json",
                        runtime_root=runtime_root, extra_env=env, check=False,
                    )
                    payload = json.loads(machine.stdout)
                    self.assertEqual(payload["error"], "activation_invalid")
                    self.assertEqual(payload["details"]["activation_problems"][0]["command"], command)

    def test_autonomy_update_diagnoses_every_invalid_step_before_packaging(self) -> None:
        vehicle_id = "picar-test"
        for step in STEPS:
            with self.subTest(step=step), tempfile.TemporaryDirectory() as tmp:
                runtime_root = Path(tmp) / "vehicles"
                bundle = runtime_root / vehicle_id / "bundle"
                path = bundle / "runtime" / step / "active.json"
                path.parent.mkdir(parents=True)
                original = json.dumps({"schema": f"automa_{step}_activation_v0"})
                path.write_text(original, encoding="utf-8")
                result = invoke_automa(
                    "vehicles", "update", "autonomy", "--id", vehicle_id,
                    "--skip-discovery", "--ssh-target", "pi@127.0.0.1",
                    runtime_root=runtime_root, check=False,
                )
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertEqual(result.stderr, "")
                self.assertIn(f"Invalid {step} activation:", result.stdout)
                self.assertIn(f"./cli/automa vehicles update {step} --id {vehicle_id}", result.stdout)
                self.assertEqual(path.read_text(encoding="utf-8"), original)
                self.assertEqual(list(bundle.iterdir()), [bundle / "runtime"])
                self.assertEqual(list((bundle / "runtime").iterdir()), [path.parent])


if __name__ == "__main__":
    unittest.main(verbosity=2)
