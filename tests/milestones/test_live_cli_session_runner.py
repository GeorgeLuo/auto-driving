from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNNER = (
    ROOT
    / "docs"
    / "milestones"
    / "007-cli-operator-usability"
    / "tools"
    / "live-cli-session-runner"
    / "session_runner.py"
)
CATALOGS = RUNNER.parent / "catalogs"


class LiveCliSessionRunnerTests(unittest.TestCase):
    def test_runner_script_exists(self) -> None:
        self.assertTrue(RUNNER.is_file())
        self.assertTrue((CATALOGS / "m007-acceptance.yaml").is_file())
        self.assertTrue((CATALOGS / "exploratory-discovery.yaml").is_file())

    def test_list_catalogs(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(RUNNER), "--list-catalogs"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("m007-acceptance.yaml", completed.stdout)
        self.assertIn("exploratory-discovery.yaml", completed.stdout)

    def test_dry_run_acceptance_writes_structured_result(self) -> None:
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("PyYAML is required to load catalog YAML")

        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp) / "session"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(RUNNER),
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
                    "--browser-name",
                    "TestBrowser",
                    "--browser-version",
                    "0",
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertIn(completed.returncode, {0, 1, 2}, completed.stdout + completed.stderr)
            result_path = session_dir / "result.json"
            self.assertTrue(result_path.is_file(), completed.stdout + completed.stderr)
            result = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual(result["schema"], "live_cli_session_result_v0")
            self.assertEqual(result["catalog"]["id"], "m007-acceptance")
            self.assertEqual(result["catalog"]["track"], "acceptance")
            # Required visual gates skipped in non-interactive dry-run => incomplete.
            self.assertEqual(result["result"], "incomplete")
            self.assertTrue(result["ordered_step_outcomes"])
            self.assertTrue((session_dir / "human-notes.md").is_file())
            self.assertTrue((session_dir / "findings.jsonl").is_file())
            self.assertTrue((session_dir / "digests.json").is_file())
            self.assertTrue((session_dir / "baseline.json").is_file())
            # Every step has an envelope.
            for step in result["ordered_step_outcomes"]:
                envelope = session_dir / "steps" / step["id"] / "envelope.json"
                self.assertTrue(envelope.is_file(), step["id"])

    def test_acceptance_catalog_declares_required_gates(self) -> None:
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML is required to load catalog YAML")

        catalog = yaml.safe_load(
            (CATALOGS / "m007-acceptance.yaml").read_text(encoding="utf-8")
        )
        gate_ids = {gate["id"] for gate in catalog["gates"]}
        self.assertIn("human_view", gate_ids)
        self.assertIn("startup", gate_ids)
        self.assertIn("cleanup", gate_ids)
        step_ids = [step["id"] for step in catalog["steps"]]
        self.assertEqual(
            step_ids[:3],
            ["baseline", "help-top", "help-vehicles"],
        )
        self.assertIn("automation-run", step_ids)
        self.assertIn("status-stopped", step_ids)


if __name__ == "__main__":
    unittest.main(verbosity=2)
