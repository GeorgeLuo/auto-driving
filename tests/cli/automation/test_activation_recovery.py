from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from autonomy.decision_cycle.activation import STEPS
from tests.support.cli_runner import run_automa
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
                    human = run_automa(
                        "vehicles", "status", "--id", VEHICLE_ID,
                        runtime_root=runtime_root, extra_env=env, check=False,
                    )
                    machine = run_automa(
                        "vehicles", "status", "--id", VEHICLE_ID, "--json",
                        runtime_root=runtime_root, extra_env=env, check=False,
                    )
                    payload = json.loads(machine.stdout)
                    self.assertEqual(human.returncode, 1, human.stdout + human.stderr)
                    self.assertEqual(machine.returncode, 1)
                    self.assertIn(f"Invalid {step} activation:", human.stdout)
                    self.assertIn(f"Next action: {command}", human.stdout)
                    self.assertEqual(payload["layers"]["automation_deployment"]["state"], "invalid")
                    self.assertEqual(payload["next_action"]["reason"], "step_activation_invalid")
                    self.assertEqual(payload["next_action"]["command"], command)
                    details = payload["layers"]["automation_deployment"]["details"]
                    self.assertEqual(details["activation_problems"][0]["step"], step)
                    automation = json.loads(run_automa(
                        "vehicles", "automation", "status", "--id", VEHICLE_ID, "--json",
                        runtime_root=runtime_root, extra_env=env,
                    ).stdout)
                    self.assertEqual(automation["outcome"]["status"], "degraded")
                    self.assertEqual(automation["vehicles"][0]["process"]["recovery"], command)
                    for verb in ("run", "restart"):
                        result = run_automa(
                            "vehicles", "automation", verb, "--id", VEHICLE_ID,
                            "--observe-only", "--frames", "0", "--open-view",
                            runtime_root=runtime_root, extra_env=env, check=False,
                        )
                        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                        self.assertIn(command, result.stdout)
                        self.assertEqual(result.stderr, "")
                        self.assertFalse(paths.automation_process.exists())
                        self.assertFalse(paths.automation_state.exists())
                    self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_invalid_restart_preserves_an_existing_process_and_its_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
            try:
                paths = write_runtime_fixture(runtime_root, VEHICLE_ID, pid=child.pid)
                path = paths.bundle_root / "runtime/memory/active.json"
                path.parent.mkdir(parents=True)
                path.write_text('{"schema":"automa_memory_activation_v0"}', encoding="utf-8")
                process = paths.automation_process.read_bytes()
                state = paths.automation_state.read_bytes()
                result = run_automa(
                    "vehicles", "automation", "restart", "--id", VEHICLE_ID,
                    "--observe-only", runtime_root=runtime_root, check=False,
                )
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn("vehicles update memory", result.stdout)
                self.assertIsNone(child.poll())
                self.assertEqual(paths.automation_process.read_bytes(), process)
                self.assertEqual(paths.automation_state.read_bytes(), state)
            finally:
                child.terminate()
                child.wait(timeout=5)

    def test_perception_readiness_rejects_old_memory_and_explicit_restage_recovers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, fake_metrics_ui_server() as ws_url:
            runtime_root = Path(tmp) / "vehicles"
            path = runtime_root / VEHICLE_ID / "bundle/runtime/memory/active.json"
            path.parent.mkdir(parents=True)
            original = json.dumps({"schema": "automa_memory_activation_v0"})
            path.write_text(original, encoding="utf-8")
            env = {"CHASE_UI_WS_URL": ws_url}
            update = run_automa(
                "vehicles", "update", "perception", "--id", VEHICLE_ID,
                "--preset", "lightweight_observer", "--json",
                runtime_root=runtime_root, extra_env=env, check=False,
            )
            self.assertEqual(update.returncode, 2, update.stdout + update.stderr)
            payload = json.loads(update.stdout)
            self.assertEqual(payload["readiness"]["status"], "blocked")
            self.assertEqual(payload["readiness"]["blocking_layer"], "automation_deployment")
            self.assertEqual(payload["next_action"]["reason"], "step_activation_invalid")
            self.assertEqual(path.read_text(encoding="utf-8"), original)
            automation = json.loads(run_automa(
                "vehicles", "automation", "status", "--id", VEHICLE_ID, "--json",
                runtime_root=runtime_root, extra_env=env,
            ).stdout)
            self.assertEqual(automation["outcome"]["status"], "degraded")
            self.assertEqual(automation["vehicles"][0]["activation_problems"][0]["step"], "memory")
            run_automa(
                "vehicles", "update", "memory", "--id", VEHICLE_ID,
                "--preset", "recency_ledger", runtime_root=runtime_root, extra_env=env,
            )
            status = json.loads(run_automa(
                "vehicles", "status", "--id", VEHICLE_ID, "--json",
                runtime_root=runtime_root, extra_env=env,
            ).stdout)
            self.assertEqual(status["layers"]["automation_deployment"]["state"], "deployed")
            self.assertEqual(status["next_action"]["reason"], "worker_stopped")

    def test_perception_update_diagnoses_invalid_steps_it_refreshes_before_writing(self) -> None:
        for step in ("observation", "proposal", "plan", "action"):
            for contents in ('{"schema":"retired"}', '{"broken":'):
                with self.subTest(step=step, contents=contents), tempfile.TemporaryDirectory() as tmp:
                    runtime_root = Path(tmp) / "vehicles"
                    bundle = runtime_root / VEHICLE_ID / "bundle"
                    path = bundle / "runtime" / step / "active.json"
                    path.parent.mkdir(parents=True)
                    path.write_text(contents, encoding="utf-8")
                    for flags in ([], ["--json"]):
                        result = run_automa(
                            "vehicles", "update", "perception", "--id", VEHICLE_ID, *flags,
                            runtime_root=runtime_root, check=False,
                        )
                        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                        self.assertEqual(result.stderr, "")
                        if flags:
                            payload = json.loads(result.stdout)
                            self.assertEqual(payload["error"], "invalid_activation")
                            problem = payload["activation_problems"][0]
                            self.assertEqual(problem["step"], step)
                            self.assertIn(f"vehicles update {step}", problem["command"])
                        else:
                            self.assertIn(f"vehicles update {step}", result.stdout)
                        self.assertEqual(path.read_text(encoding="utf-8"), contents)
                        self.assertFalse((bundle / "runtime/perception/active.json").exists())
                        self.assertFalse((bundle / "releases").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
