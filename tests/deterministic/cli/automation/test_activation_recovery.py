from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from tests.support.runtime_fixtures import write_host_fixture

from autonomy.decision_cycle.activation import DECISION_STEPS, STEPS
from tests.shared.cli.invoke import invoke_automa
from tests.support.fake_metrics_ui import fake_metrics_ui_server
from tests.support.runtime_fixtures import write_runtime_fixture


HOST_ID = "chase-sim-chaser"


class StagedActivationRecoveryTests(unittest.TestCase):
    def test_every_invalid_step_has_the_same_human_and_json_recovery_without_launching(self) -> None:
        with fake_metrics_ui_server() as ws_url:
            for step in STEPS:
                with self.subTest(step=step), tempfile.TemporaryDirectory() as tmp:
                    runtime_root = Path(tmp) / "vehicles"
                    write_host_fixture(runtime_root, HOST_ID)
                    paths = write_runtime_fixture(runtime_root, HOST_ID, pid=999_999_999)
                    paths.automation_process.unlink()
                    paths.automation_state.unlink()
                    path = paths.bundle_root / "runtime" / step / "active.json"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    original = json.dumps({"schema": f"automa_{step}_activation_v0"})
                    path.write_text(original, encoding="utf-8")
                    env = {"CHASE_UI_WS_URL": ws_url}
                    command = f"./cli/automa host stage {step} --id {HOST_ID}"
                    human = invoke_automa(
                        "host", "status", "--id", HOST_ID,
                        runtime_root=runtime_root, extra_env=env,
                    )
                    self.assertIn(f"Invalid {step} activation:", human.stdout)
                    self.assertIn(f"next: {command}", human.stdout)
                    automation = json.loads(invoke_automa(
                        "host", "status", "--id", HOST_ID, "--json",
                        runtime_root=runtime_root, extra_env=env,
                    ).stdout)
                    self.assertEqual(automation["outcome"]["status"], "degraded")
                    self.assertEqual(automation["hosts"][0]["activation_problems"][0]["step"], step)
                    self.assertEqual(automation["hosts"][0]["process"]["recovery"], command)
                    for verb in ("run", "restart"):
                        result = invoke_automa(
                            "host", verb, "--id", HOST_ID,
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
            write_host_fixture(runtime_root, HOST_ID)
            path = runtime_root / HOST_ID / "bundle/runtime/memory/active.json"
            path.parent.mkdir(parents=True)
            original = json.dumps({"schema": "automa_memory_activation_v0"})
            path.write_text(original, encoding="utf-8")
            env = {"CHASE_UI_WS_URL": ws_url}
            invoke_automa(
                "host", "stage", "perception", "--id", HOST_ID,
                "--preset", "lightweight_observer", "--json",
                runtime_root=runtime_root, extra_env=env,
            )
            self.assertEqual(path.read_text(encoding="utf-8"), original)
            automation = json.loads(invoke_automa(
                "host", "status", "--id", HOST_ID, "--json",
                runtime_root=runtime_root, extra_env=env,
            ).stdout)
            self.assertEqual(automation["outcome"]["status"], "degraded")
            self.assertEqual(automation["hosts"][0]["activation_problems"][0]["step"], "memory")
            invoke_automa(
                "host", "stage", "memory", "--id", HOST_ID,
                "--preset", "recency_ledger", runtime_root=runtime_root, extra_env=env,
            )
            restaged = json.loads(invoke_automa(
                "host", "status", "--id", HOST_ID, "--json",
                runtime_root=runtime_root, extra_env=env,
            ).stdout)["hosts"][0]
            self.assertTrue(restaged["deployed"])
            self.assertFalse(restaged["process"]["running"])

    def test_info_and_stream_name_the_invalid_step_and_its_restage_command(self) -> None:
        with fake_metrics_ui_server() as ws_url:
            for step in ("perception", "memory", *DECISION_STEPS):
                with self.subTest(step=step), tempfile.TemporaryDirectory() as tmp:
                    runtime_root = Path(tmp) / "vehicles"
                    write_host_fixture(runtime_root, HOST_ID)
                    path = runtime_root / HOST_ID / "bundle/runtime" / step / "active.json"
                    path.parent.mkdir(parents=True)
                    path.write_text(json.dumps({"schema": f"automa_{step}_activation_v0"}), encoding="utf-8")
                    env = {"CHASE_UI_WS_URL": ws_url}
                    command = f"./cli/automa host stage {step} --id {HOST_ID}"
                    # Each step's info reads its own activation; a decision step's stream reads all three.
                    surfaces = [("info", step)]
                    if step in DECISION_STEPS:
                        surfaces.append(("stream", step, "--once"))
                    for surface in surfaces:
                        result = invoke_automa(
                            "host", *surface, "--id", HOST_ID,
                            runtime_root=runtime_root, extra_env=env, check=False,
                        )
                        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                        self.assertEqual(result.stderr, "")
                        self.assertIn(f"Invalid {step} activation:", result.stdout)
                        self.assertIn(f"Restage with your intended selection: {command}", result.stdout)
                    if step not in DECISION_STEPS:
                        continue
                    machine = invoke_automa(
                        "host", "stream", step, "--once", "--id", HOST_ID, "--json",
                        runtime_root=runtime_root, extra_env=env, check=False,
                    )
                    payload = json.loads(machine.stdout)
                    self.assertEqual(payload["error"], "activation_invalid")
                    self.assertEqual(payload["details"]["activation_problems"][0]["command"], command)

    def test_autonomy_update_diagnoses_every_invalid_step_before_packaging(self) -> None:
        host_id = "picar-test"
        for step in STEPS:
            with self.subTest(step=step), tempfile.TemporaryDirectory() as tmp:
                runtime_root = Path(tmp) / "vehicles"
                write_host_fixture(runtime_root, HOST_ID)
                write_host_fixture(runtime_root, host_id, provider="picar")
                bundle = runtime_root / host_id / "bundle"
                path = bundle / "runtime" / step / "active.json"
                path.parent.mkdir(parents=True)
                original = json.dumps({"schema": f"automa_{step}_activation_v0"})
                path.write_text(original, encoding="utf-8")
                result = invoke_automa(
                    "host", "update", "--id", host_id,
                    runtime_root=runtime_root, check=False,
                )
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertEqual(result.stderr, "")
                self.assertIn(f"Invalid {step} activation:", result.stdout)
                self.assertIn(f"./cli/automa host stage {step} --id {host_id}", result.stdout)
                self.assertEqual(path.read_text(encoding="utf-8"), original)
                self.assertEqual(list(bundle.iterdir()), [bundle / "runtime"])
                self.assertEqual(list((bundle / "runtime").iterdir()), [path.parent])


if __name__ == "__main__":
    unittest.main(verbosity=2)
