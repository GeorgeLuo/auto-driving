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


HOST_NAME = "chase-sim-chaser"


class StagedActivationRecoveryTests(unittest.TestCase):
    def test_every_invalid_step_has_the_same_human_and_json_recovery_without_launching(self) -> None:
        with fake_metrics_ui_server() as ws_url:
            for step in STEPS:
                with self.subTest(step=step), tempfile.TemporaryDirectory() as tmp:
                    runtime_root = Path(tmp) / "vehicles"
                    write_host_fixture(runtime_root, HOST_NAME)
                    paths = write_runtime_fixture(runtime_root, HOST_NAME, pid=999_999_999)
                    paths.automation_process.unlink()
                    paths.automation_state.unlink()
                    path = paths.bundle_root / "runtime" / step / "active.json"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    original = json.dumps({"schema": f"automa_{step}_activation_v0"})
                    path.write_text(original, encoding="utf-8")
                    env = {"CHASE_UI_WS_URL": ws_url}
                    command = f"./cli/automa hosts update --name {HOST_NAME}"
                    human = invoke_automa(
                        "hosts", "status", "--long", runtime_root=runtime_root, extra_env=env,
                    )
                    self.assertIn(f"Invalid {step} activation:", human.stdout)
                    self.assertIn(f"Replace it with the step's default: {command}", human.stdout)
                    self.assertNotIn("next:", human.stdout)
                    run = json.loads(invoke_automa(
                        "hosts", "status", "--json", runtime_root=runtime_root, extra_env=env,
                    ).stdout)["hosts"][0]["run"]
                    self.assertEqual(run["activation_problems"][0]["step"], step)
                    self.assertEqual(run["activation_problems"][0]["command"], command)
                    self.assertNotIn("recovery", run["process"])
                    for verb in ("run", "restart"):
                        result = invoke_automa(
                            "hosts", verb, "--name", HOST_NAME,
                            "--observe-only", "--num-decisions", "0", "--open-view",
                            runtime_root=runtime_root, extra_env=env, check=False,
                        )
                        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                        self.assertIn(command, result.stdout)
                        self.assertEqual(result.stderr, "")
                        self.assertFalse(paths.automation_process.exists())
                        self.assertFalse(paths.automation_state.exists())
                    self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_an_update_replaces_an_invalid_document_and_status_recovers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, fake_metrics_ui_server() as ws_url:
            runtime_root = Path(tmp) / "vehicles"
            write_host_fixture(runtime_root, HOST_NAME)
            path = runtime_root / HOST_NAME / "bundle/runtime/memory/active.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"schema": "automa_memory_activation_v0"}), encoding="utf-8")
            env = {"CHASE_UI_WS_URL": ws_url}
            run = json.loads(invoke_automa(
                "hosts", "status", "--json", runtime_root=runtime_root, extra_env=env,
            ).stdout)["hosts"][0]["run"]
            self.assertEqual(run["activation_problems"][0]["step"], "memory")
            invoke_automa("hosts", "update", "--name", HOST_NAME, runtime_root=runtime_root, extra_env=env)
            updated = json.loads(invoke_automa(
                "hosts", "status", "--json", runtime_root=runtime_root, extra_env=env,
            ).stdout)["hosts"][0]["run"]
            self.assertFalse(updated.get("activation_problems"))
            self.assertTrue(updated["deployed"])
            self.assertFalse(updated["process"]["running"])

    def test_info_and_stream_name_the_invalid_step_and_the_update_that_replaces_it(self) -> None:
        with fake_metrics_ui_server() as ws_url:
            for step in ("perception", "memory", *DECISION_STEPS):
                with self.subTest(step=step), tempfile.TemporaryDirectory() as tmp:
                    runtime_root = Path(tmp) / "vehicles"
                    write_host_fixture(runtime_root, HOST_NAME)
                    path = runtime_root / HOST_NAME / "bundle/runtime" / step / "active.json"
                    path.parent.mkdir(parents=True)
                    path.write_text(json.dumps({"schema": f"automa_{step}_activation_v0"}), encoding="utf-8")
                    env = {"CHASE_UI_WS_URL": ws_url}
                    command = f"./cli/automa hosts update --name {HOST_NAME}"
                    # Each step's info reads its own activation; a decision step's stream reads all three.
                    surfaces = [("info", step)]
                    if step in DECISION_STEPS:
                        surfaces.append(("stream", step, "--once"))
                    for surface in surfaces:
                        result = invoke_automa(
                            "hosts", *surface, "--name", HOST_NAME,
                            runtime_root=runtime_root, extra_env=env, check=False,
                        )
                        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                        self.assertEqual(result.stderr, "")
                        self.assertIn(f"Invalid {step} activation:", result.stdout)
                        self.assertIn(f"Replace it with the step's default: {command}", result.stdout)
                    if step not in DECISION_STEPS:
                        continue
                    machine = invoke_automa(
                        "hosts", "stream", step, "--once", "--name", HOST_NAME, "--json",
                        runtime_root=runtime_root, extra_env=env, check=False,
                    )
                    payload = json.loads(machine.stdout)
                    self.assertEqual(payload["error"], "activation_invalid")
                    self.assertEqual(payload["details"]["activation_problems"][0]["command"], command)


if __name__ == "__main__":
    unittest.main(verbosity=2)
