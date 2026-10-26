from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from tests.support.runtime_fixtures import write_host_fixture

from autonomy.decision_cycle.steps import STEP_RUNNERS
from tests.shared.cli.invoke import invoke_automa


class DecisionCommandTests(unittest.TestCase):
    def test_update_steps_then_info_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            write_host_fixture(runtime_root, "chase-sim-chaser")
            vehicle = ("--id", "chase-sim-chaser", "--json")
            runtime = runtime_root / "chase-sim-chaser" / "bundle" / "runtime"

            dry_run = invoke_automa(
                "hosts", "stage", "action", *vehicle, "--plugin", "mode", "--dry-run",
                runtime_root=runtime_root,
            )
            self.assertEqual(dry_run.returncode, 0, dry_run.stderr + dry_run.stdout)
            self.assertFalse((runtime / "action" / "active.json").exists())

            for step, plugins in (("proposal", ()), ("action", ("--plugin", "mode"))):
                update = invoke_automa(
                    "hosts", "stage", step, *vehicle, *plugins,
                    runtime_root=runtime_root,
                )
                self.assertEqual(update.returncode, 0, update.stderr + update.stdout)
                self.assertEqual(json.loads(update.stdout)["step"], step)
            action = json.loads((runtime / "action" / "active.json").read_text())
            self.assertEqual(action["plugins"], ["mode"])

            def snapshot_files() -> dict[Path, bytes]:
                return {
                    path.relative_to(runtime_root): path.read_bytes()
                    for path in runtime_root.rglob("*")
                    if path.is_file()
                }

            before_info = snapshot_files()
            unstaged = invoke_automa(
                "hosts", "info", "plan", *vehicle, runtime_root=runtime_root, check=False,
            )
            self.assertEqual(unstaged.returncode, 2, unstaged.stdout)
            self.assertIn("./cli/automa hosts stage plan --id", unstaged.stdout)

            for step, plugins in (("proposal", ["avoid_recent_obstruction"]), ("action", ["mode"])):
                info = invoke_automa("hosts", "info", step, *vehicle, runtime_root=runtime_root)
                self.assertEqual(info.returncode, 0, info.stderr + info.stdout)
                payload = json.loads(info.stdout)
                self.assertEqual(payload["schema"], f"host_{step}_info")
                self.assertEqual(payload["activation"]["plugins"], plugins)
                self.assertEqual(payload["decision"]["plugins"]["action"], ["mode"])
                self.assertEqual(payload["decision"]["authority"]["gate_id"], "mode")
                # No runtime host runs, so the probe has no step to report.
                self.assertEqual(payload["live"]["schema"], f"host_{step}_live")
                self.assertEqual(payload["live"]["status"], "unavailable")
                self.assertEqual(
                    f"{step}_schema" in payload, STEP_RUNNERS[step].schema_name is not None
                )
            self.assertEqual(before_info, snapshot_files())


if __name__ == "__main__":
    unittest.main(verbosity=2)
