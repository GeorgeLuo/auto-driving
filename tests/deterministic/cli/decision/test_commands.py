from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from tests.support.runtime_fixtures import stage_step

from autonomy.decision_cycle.steps import STEP_RUNNERS
from tests.shared.cli.invoke import invoke_automa


class DecisionCommandTests(unittest.TestCase):
    def test_info_reports_the_staged_steps_and_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            vehicle = ("--name", "chase-sim-chaser", "--json")
            stage_step(runtime_root, "chase-sim-chaser", "proposal")
            stage_step(runtime_root, "chase-sim-chaser", "action", plugins=["mode"])

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
            self.assertIn("./cli/automa hosts update --name chase-sim-chaser", unstaged.stdout)

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
