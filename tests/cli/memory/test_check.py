from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cli.automa_cli.memory_check import (
    build_default_memory_check_phases,
    publication_to_check_frame,
    run_vehicle_memory_check,
    score_memory_check_phase,
)
from tests.support.cli_runner import run_automa


def _live_publication(
    *,
    frame_id: str,
    frame_index: int,
    with_boundary: bool,
    steering: float = 0.0,
    throttle: float = 0.0,
) -> dict:
    things = []
    if with_boundary:
        things.append(
            {
                "thing_id": "floor_boundary_000",
                "kind": "floor_boundary",
                "label": "boundary",
                "confidence": 0.9,
                "location": {
                    "frame": "image",
                    "zone": "center",
                    "bbox_xyxy_norm": [0.3, 0.4, 0.7, 0.95],
                },
                "source_plugin_id": "floor-plane-v0",
            }
        )
    return {
        "health": "healthy",
        "drive_mode": "user",
        "control": {"steering": steering, "throttle": throttle},
        "frame": {
            "frame_id": frame_id,
            "frame_index": frame_index,
            "captured_at_ms": 1_000 + frame_index * 100,
            "completed_at_ms": 1_010 + frame_index * 100,
            "has_image": True,
        },
        "perception": {
            "plugin_id": "lightweight_observer",
            "status": "ok",
            "things": things,
            "signals": [{"signal_id": "floor_visible", "value": True, "confidence": 0.95}],
            "lines": ["live test"],
        },
        "observation": {
            "observation_id": f"obs_{frame_id}",
            "created_at_ms": 1_000 + frame_index * 100,
            "sensor_snapshot": {},
            "perception_plugin_id": "lightweight_observer",
            "things": things,
            "signals": [{"signal_id": "floor_visible", "value": True, "confidence": 0.95}],
        },
        "memory": {
            "health": "healthy" if with_boundary else "empty",
            "record_count": 1 if with_boundary else 0,
            "records": [],
        },
    }


class MemoryCheckTests(unittest.TestCase):
    def test_check_help_is_registered(self) -> None:
        result = run_automa("vehicles", "memory", "help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("check", result.stdout)

    def test_default_phases_cover_lifecycle(self) -> None:
        phases = build_default_memory_check_phases()
        names = [phase["name"] for phase in phases]
        self.assertEqual(names, ["present", "dropout", "expiry", "reset"])

    def test_score_helpers(self) -> None:
        present = score_memory_check_phase(
            phase_name="present",
            final={
                "health": "healthy",
                "record_count": 1,
                "epoch_id": "epoch-1",
                "records": [{"record_id": "thing:floor_boundary_000"}],
            },
            present_keys=set(),
            prior_epoch=None,
        )
        self.assertTrue(present["passed"])
        dropout = score_memory_check_phase(
            phase_name="dropout",
            final={
                "health": "healthy",
                "record_count": 1,
                "records": [{"record_id": "thing:floor_boundary_000"}],
            },
            present_keys={"thing:floor_boundary_000"},
            prior_epoch="epoch-1",
        )
        self.assertTrue(dropout["passed"])
        expiry = score_memory_check_phase(
            phase_name="expiry",
            final={"health": "empty", "record_count": 0, "records": []},
            present_keys={"thing:floor_boundary_000"},
            prior_epoch="epoch-1",
        )
        self.assertTrue(expiry["passed"])
        reset = score_memory_check_phase(
            phase_name="reset",
            final={"health": "empty", "record_count": 0, "epoch_id": "epoch-2", "records": []},
            present_keys=set(),
            prior_epoch="epoch-1",
        )
        self.assertTrue(reset["passed"])

    def test_run_memory_check_passes_offline(self) -> None:
        result = run_vehicle_memory_check(
            vehicle_id="chase-sim-chaser",
            implementation_id="bounded_evidence",
            json_output=True,
            skip_discovery=True,
        )
        self.assertEqual(result.exit_code, 0, result.message)
        payload = json.loads(result.message)
        self.assertEqual(payload["schema"], "vehicle_memory_check_v0")
        self.assertTrue(payload["passed"])
        self.assertEqual(
            [item["phase"] for item in payload["phase_results"]],
            ["present", "dropout", "expiry", "reset"],
        )
        self.assertTrue(all(item["passed"] for item in payload["phase_results"]))
        self.assertFalse(payload["safety"]["movement_commands_sent"])
        self.assertGreaterEqual(len(payload["provenance_rows"]), 1)

    def test_run_memory_check_record_writes_extract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp) / "memory-check"
            result = run_vehicle_memory_check(
                vehicle_id="chase-sim-chaser",
                implementation_id="bounded_evidence",
                record=True,
                json_output=True,
                skip_discovery=True,
                output_root=output_root,
            )
            self.assertEqual(result.exit_code, 0, result.message)
            payload = json.loads(result.message)
            self.assertTrue(payload["recorded"])
            run_dirs = list(output_root.iterdir())
            self.assertEqual(len(run_dirs), 1)
            record_dir = run_dirs[0]
            for name in (
                "manifest.json",
                "report.json",
                "sequence.json",
                "present_memory.json",
                "provenance_extract.html",
            ):
                self.assertTrue((record_dir / name).is_file(), name)

    def test_cli_memory_check_json(self) -> None:
        result = run_automa(
            "vehicles",
            "memory",
            "check",
            "--id",
            "chase-sim-chaser",
            "--implementation",
            "bounded_evidence",
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["passed"])

    def test_cli_memory_check_record_env_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp) / "memory-check"
            env_key = "AUTOMA_MEMORY_CHECK_OUTPUT_ROOT"
            previous = os.environ.get(env_key)
            os.environ[env_key] = str(output_root)
            try:
                result = run_automa(
                    "vehicles",
                    "memory",
                    "check",
                    "--id",
                    "chase-sim-chaser",
                    "--implementation",
                    "bounded_evidence",
                    "--record",
                    "--json",
                )
            finally:
                if previous is None:
                    os.environ.pop(env_key, None)
                else:
                    os.environ[env_key] = previous
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertTrue(payload["recorded"])
            self.assertTrue(output_root.exists())

    def test_publication_to_check_frame_force_empty(self) -> None:
        pub = _live_publication(frame_id="f1", frame_index=0, with_boundary=True)
        frame = publication_to_check_frame(pub, index=0, force_empty=True)
        self.assertEqual(frame["observation"]["things"], [])
        self.assertEqual(frame["frame_id"], "f1")

    def test_physical_pi_path_with_mocked_publications(self) -> None:
        vehicle = {
            "vehicle_id": "piracer",
            "provider": "picar",
            "connection": {"base_url": "http://piracer.test:8887"},
        }
        pubs = [
            _live_publication(frame_id="present_frame", frame_index=0, with_boundary=True),
            _live_publication(frame_id="dropout_frame", frame_index=1, with_boundary=False),
        ]
        calls = {"n": 0}

        def fake_pub(_url: str) -> dict:
            idx = min(calls["n"], len(pubs) - 1)
            calls["n"] += 1
            return pubs[idx]

        def fake_frame(_url: str) -> tuple[bytes, dict[str, str]]:
            return b"jpeg-bytes", {"content-type": "image/jpeg", "x-frame-id": "present_frame"}

        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp) / "memory-check"
            with mock.patch(
                "cli.automa_cli.memory_check.discover_active_vehicles",
                return_value={"vehicles": [vehicle]},
            ), mock.patch(
                "cli.automa_cli.memory_check.find_vehicle_by_id",
                return_value=(vehicle, None),
            ):
                result = run_vehicle_memory_check(
                    vehicle_id="piracer",
                    implementation_id="bounded_evidence",
                    record=True,
                    json_output=True,
                    auto=True,
                    fetch_publication=fake_pub,
                    fetch_frame=fake_frame,
                    output_root=output_root,
                )
            self.assertEqual(result.exit_code, 0, result.message)
            payload = json.loads(result.message)
            self.assertTrue(payload["passed"])
            self.assertEqual(payload["provider"], "picar")
            self.assertEqual(payload["safety"]["action_policy"], "physical_observe_only")
            self.assertFalse(payload["safety"]["movement_commands_sent"])
            present = next(item for item in payload["phase_results"] if item["phase"] == "present")
            self.assertTrue(present["live_control_zero"])
            self.assertIn("present_frame", present["live_frame_ids"])
            self.assertTrue(payload["recorded"])
            run_dir = next(output_root.iterdir())
            self.assertTrue((run_dir / "frames").is_dir())
            self.assertTrue(any((run_dir / "frames").iterdir()))
            extract = (run_dir / "provenance_extract.html").read_text(encoding="utf-8")
            self.assertIn("retained evidence", extract.lower())
            self.assertIn("present_frame", extract)

    def test_physical_pi_rejects_non_zero_control(self) -> None:
        vehicle = {
            "vehicle_id": "piracer",
            "provider": "picar",
            "connection": {"base_url": "http://piracer.test:8887"},
        }
        bad = _live_publication(
            frame_id="moving",
            frame_index=0,
            with_boundary=True,
            steering=0.2,
            throttle=0.0,
        )
        with mock.patch(
            "cli.automa_cli.memory_check.discover_active_vehicles",
            return_value={"vehicles": [vehicle]},
        ), mock.patch(
            "cli.automa_cli.memory_check.find_vehicle_by_id",
            return_value=(vehicle, None),
        ):
            result = run_vehicle_memory_check(
                vehicle_id="piracer",
                auto=True,
                json_output=True,
                fetch_publication=lambda _url: bad,
            )
        self.assertEqual(result.exit_code, 2)
        self.assertIn("non-zero", result.message)


if __name__ == "__main__":
    unittest.main(verbosity=2)
