from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


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
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, RUNNER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _aggregate_initial_status() -> dict:
    """Real aggregate shape: top-level layers null, card under vehicles[]."""
    return {
        "schema": "automa_vehicle_status_list_v1",
        "layers": None,
        "vehicle_id": None,
        "vehicles": [
            {
                "schema": "automa_vehicle_status_v1",
                "vehicle_id": "chase-sim-chaser",
                "layers": {
                    "simulator_server": {"state": "reachable"},
                    "simulator_frontend": {"state": "connected"},
                    "chase_game": {"state": "ready"},
                    "vehicle": {"state": "discoverable"},
                    "passive_capture": {"state": "available"},
                    "automation_deployment": {"state": "deployed"},
                    "automation_worker": {"state": "stopped"},
                    "perception_view": {"state": "stale"},
                },
            }
        ],
    }


def _current_view_payload(**overrides) -> dict:
    payload = {
        "schema": "automa_perception_view_publication_v1",
        "vehicle_id": "chase-sim-chaser",
        "frame": {"frame_id": "chase_frame_1", "frame_index": 1},
        "overlay": {
            "status": "current",
            "source_frame_id": "chase_frame_1",
            "frame_lag": 0,
        },
        "perception": {"things": [{"thing_id": "x"}], "signals": []},
        "cycle": {
            "action_policy": "observe_only",
            "control_application": "not_applied",
        },
        "control": {"applied": False, "steering": 0.0, "throttle": 0.0},
    }
    payload.update(overrides)
    return payload


class LiveCliSessionRunnerTests(unittest.TestCase):
    def test_runner_script_exists(self) -> None:
        self.assertTrue(RUNNER_PATH.is_file())
        self.assertTrue((CATALOGS / "m007-acceptance.yaml").is_file())

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
            self.assertFalse(any(e["path"] == "digests.json" for e in digests["artifacts"]))

    def test_extract_vehicle_status_from_aggregate(self) -> None:
        runner = _load_runner_module()
        aggregate = _aggregate_initial_status()
        card = runner.extract_vehicle_status(aggregate, "chase-sim-chaser")
        self.assertIsNotNone(card)
        ok, msg = runner.validate_initial_layers(aggregate, vehicle_id="chase-sim-chaser")
        self.assertTrue(ok, msg)
        # Broken: only top-level layers=null without vehicles
        ok, msg = runner.validate_initial_layers({"layers": None}, vehicle_id="chase-sim-chaser")
        self.assertFalse(ok)

    def test_view_correlation_real_publication_shapes(self) -> None:
        runner = _load_runner_module()
        ok, msg = runner.validate_view_latest(_current_view_payload())
        self.assertTrue(ok, msg)

        ok, msg = runner.validate_view_latest(
            _current_view_payload(overlay={"status": "pending", "source_frame_id": None})
        )
        self.assertFalse(ok)
        self.assertIn("overlay.status", msg)

        ok, msg = runner.validate_view_latest(
            _current_view_payload(
                frame={"frame_id": "chase_frame_2", "frame_index": 2},
                overlay={"status": "stale", "source_frame_id": "chase_frame_1", "frame_lag": 1},
            )
        )
        self.assertFalse(ok)

        ok, msg = runner.validate_view_latest(
            _current_view_payload(
                overlay={"status": "current", "source_frame_id": "other"},
            )
        )
        self.assertFalse(ok)
        self.assertIn("source_frame_id", msg)

        ok, msg = runner.validate_view_latest(_current_view_payload(perception=None))
        self.assertFalse(ok)
        self.assertIn("perception", msg)

        ok, msg = runner.validate_view_latest(
            _current_view_payload(control={"applied": True, "steering": 0.1, "throttle": 0.2})
        )
        self.assertFalse(ok)
        self.assertIn("applied", msg)

        # Synthetic top-level frame_id must not pass without overlay/perception.
        ok, msg = runner.validate_view_latest({"frame_id": "x", "perception_frame_id": "x"})
        self.assertFalse(ok)

    def test_authority_requires_explicit_recording_false(self) -> None:
        runner = _load_runner_module()
        status = {
            "vehicle_id": "chase-sim-chaser",
            "layers": {
                "automation_worker": {
                    "details": {
                        "authority": {
                            "action_policy": "observe_only",
                            "control_application": "not_applied",
                            "recording": None,
                        }
                    }
                }
            },
        }
        ok, msg = runner.validate_authority(status, vehicle_id="chase-sim-chaser")
        self.assertFalse(ok)
        self.assertIn("recording", msg)
        status["layers"]["automation_worker"]["details"]["authority"]["recording"] = False
        ok, msg = runner.validate_authority(status, vehicle_id="chase-sim-chaser")
        self.assertTrue(ok, msg)

    def test_stopped_layers_require_deployed(self) -> None:
        runner = _load_runner_module()
        status = {
            "vehicle_id": "chase-sim-chaser",
            "layers": {
                "automation_worker": {"state": "stopped"},
                "perception_view": {"state": "stale"},
                "automation_deployment": {"state": "invalid"},
            },
        }
        ok, msg = runner.validate_stopped_layers(status, vehicle_id="chase-sim-chaser")
        self.assertFalse(ok)
        self.assertIn("deployed", msg)

    def test_browser_view_image_rejects_empty(self) -> None:
        runner = _load_runner_module()
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "browser-view.png"
            empty.write_bytes(b"")
            ok, msg = runner.validate_browser_view_image(empty)
            self.assertFalse(ok)
            good = Path(tmp) / "good.png"
            Image.new("RGB", (8, 8), (10, 20, 30)).save(good)
            ok, msg = runner.validate_browser_view_image(good)
            self.assertTrue(ok, msg)

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
        initial = next(s for s in catalog["steps"] if s["id"] == "status-initial")
        # Capture uses --id so validators receive a vehicle card.
        cmd = " ".join(initial["capture_json"]["command"])
        self.assertIn("--id", cmd)
        running = next(s for s in catalog["steps"] if s["id"] == "status-running")
        self.assertIn("view_correlation", running["machine_validators"])
        self.assertIn("preservation", running["machine_validators"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
