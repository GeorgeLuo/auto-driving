from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests.support.cli_runner import run_automa


class DecisionCommandTests(unittest.TestCase):
    def test_update_steps_then_info_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            vehicle = ("--id", "chase-sim-chaser", "--json")
            runtime = runtime_root / "chase-sim-chaser" / "bundle" / "runtime"

            dry_run = run_automa(
                "vehicles", "update", "action", *vehicle, "--plugin", "mode", "--dry-run",
                runtime_root=runtime_root,
            )
            self.assertEqual(dry_run.returncode, 0, dry_run.stderr + dry_run.stdout)
            self.assertFalse((runtime / "action" / "active.json").exists())

            for step, plugins in (("proposal", ()), ("action", ("--plugin", "mode"))):
                update = run_automa(
                    "vehicles", "update", step, *vehicle, *plugins,
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
            info = run_automa(
                "vehicles", "info", "proposal", *vehicle,
                runtime_root=runtime_root,
            )
            self.assertEqual(info.returncode, 0, info.stderr + info.stdout)
            payload = json.loads(info.stdout)
            self.assertEqual(payload["schema"], "vehicle_proposal_info_v1")
            self.assertEqual(payload["activation"]["plugins"], ["avoid_recent_obstruction"])
            self.assertEqual(payload["decision"]["authority"]["gate_id"], "mode")
            self.assertFalse(payload["published_view"]["available"])
            self.assertEqual(before_info, snapshot_files())


if __name__ == "__main__":
    unittest.main(verbosity=2)
