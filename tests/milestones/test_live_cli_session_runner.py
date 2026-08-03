from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = (
    ROOT
    / "docs"
    / "milestones"
    / "007-cli-operator-usability"
    / "tools"
    / "live-cli-session-runner"
    / "session_runner.py"
)
CATALOGS = RUNNER_PATH.parent / "catalogs"


def _load_runner_module():
    name = "live_cli_session_runner"
    spec = importlib.util.spec_from_file_location(name, RUNNER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class LiveCliSessionRunnerTests(unittest.TestCase):
    def test_runner_script_exists(self) -> None:
        self.assertTrue(RUNNER_PATH.is_file())
        self.assertTrue((CATALOGS / "m007-acceptance.yaml").is_file())
        self.assertTrue((CATALOGS / "exploratory-discovery.yaml").is_file())

    def test_list_catalogs(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(RUNNER_PATH), "--list-catalogs"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("m007-acceptance.yaml", completed.stdout)

    def test_dry_run_cannot_pass_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / "session"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(RUNNER_PATH),
                    "--catalog",
                    str(CATALOGS / "m007-acceptance.yaml"),
                    "--session-dir",
                    str(session_dir),
                    "--repo-root",
                    str(ROOT),
                    "--dry-run",
                    "--non-interactive",
                    "--auto-visual",
                    "pass",
                    "--browser-name",
                    "Chrome",
                    "--browser-version",
                    "999",
                    "--metrics-ui-repo",
                    str(ROOT),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 2, completed.stdout + completed.stderr)
            result = json.loads((session_dir / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(result["result"], "incomplete")
            self.assertIn("Dry-run", result.get("incomplete_reason") or "")
            self.assertEqual(result["execution_mode"], "dry_run")

    def test_digests_match_final_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / "session"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(RUNNER_PATH),
                    "--catalog",
                    str(CATALOGS / "m007-acceptance.yaml"),
                    "--session-dir",
                    str(session_dir),
                    "--repo-root",
                    str(ROOT),
                    "--dry-run",
                    "--non-interactive",
                    "--auto-visual",
                    "skip",
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertIn(completed.returncode, {0, 1, 2}, completed.stdout + completed.stderr)
            digests = json.loads((session_dir / "digests.json").read_text(encoding="utf-8"))
            runner = _load_runner_module()
            for entry in digests["artifacts"]:
                path = session_dir / entry["path"]
                self.assertTrue(path.is_file(), entry["path"])
                self.assertEqual(entry["sha256"], runner._sha256_file(path), entry["path"])
            # result.json is included and must match final bytes
            result_entry = next(e for e in digests["artifacts"] if e["path"] == "result.json")
            self.assertEqual(result_entry["sha256"], runner._sha256_file(session_dir / "result.json"))
            # digests.json must not digest itself
            self.assertFalse(any(e["path"] == "digests.json" for e in digests["artifacts"]))

    def test_machine_validators_negative_cases(self) -> None:
        runner = _load_runner_module()
        ok, _ = runner.validate_initial_layers({})
        self.assertFalse(ok)
        ok, msg = runner.validate_initial_layers(
            {
                "layers": {
                    "simulator_server": {"state": "reachable"},
                    "simulator_frontend": {"state": "disconnected"},
                    "chase_game": {"state": "ready"},
                    "vehicle": {"state": "discoverable"},
                    "passive_capture": {"state": "available"},
                }
            }
        )
        self.assertFalse(ok)
        self.assertIn("simulator_frontend", msg)

        ok, msg = runner.validate_authority(
            {
                "layers": {
                    "automation_worker": {
                        "details": {
                            "authority": {
                                "action_policy": "engine_idle",
                                "control_application": "applied",
                            }
                        }
                    }
                }
            }
        )
        self.assertFalse(ok)

        ok, msg = runner.validate_view_latest({"error": "timeout"})
        self.assertFalse(ok)
        ok, msg = runner.validate_view_latest(
            {"frame_id": "a", "perception_frame_id": "b"}
        )
        self.assertFalse(ok)
        ok, msg = runner.validate_view_latest(
            {"latest_frame_id": "chase_frame_1", "latest_perception_frame_id": "chase_frame_1"}
        )
        self.assertTrue(ok, msg)

        ok, msg = runner.validate_recording_scan(["old"], ["old", "new"])
        self.assertFalse(ok)
        ok, msg = runner.validate_recording_scan(["old"], ["old"])
        self.assertTrue(ok)

        ok, msg = runner.validate_stopped_layers(
            {
                "layers": {
                    "automation_worker": {"state": "running"},
                    "perception_view": {"state": "available"},
                    "automation_deployment": {"state": "deployed"},
                }
            }
        )
        self.assertFalse(ok)

    def test_acceptance_catalog_declares_required_gates(self) -> None:
        import yaml

        catalog = yaml.safe_load(
            (CATALOGS / "m007-acceptance.yaml").read_text(encoding="utf-8")
        )
        gate_ids = {gate["id"] for gate in catalog["gates"]}
        for required in (
            "human_view",
            "startup",
            "cleanup",
            "correlation",
            "default_recording",
            "authority",
        ):
            self.assertIn(required, gate_ids)
        # status-running must declare machine validators
        running = next(s for s in catalog["steps"] if s["id"] == "status-running")
        self.assertIn("view_correlation", running["machine_validators"])
        self.assertIn("authority", running["machine_validators"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
