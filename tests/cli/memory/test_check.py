from __future__ import annotations
import json
import os
import tempfile
import unittest
from pathlib import Path
from cli.automa_cli.memory_check import (
    build_default_memory_check_phases,
    publication_to_check_frame,
    run_vehicle_memory_check,
    score_memory_check_phase,
)
from tests.support.cli_runner import run_automa
from tests.cli.memory.check_fixtures import (
    _live_publication,
)


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
            final={
                "health": "empty",
                "record_count": 0,
                "epoch_id": "epoch-2",
                "records": [],
            },
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
            persisted_report = json.loads(
                (record_dir / "report.json").read_text(encoding="utf-8")
            )
            self.assertTrue(persisted_report["recorded"])
            self.assertTrue(
                str(persisted_report["record_dir"]).endswith(record_dir.name)
            )
            self.assertTrue(
                str(persisted_report["provenance_extract"]).endswith(
                    f"{record_dir.name}/provenance_extract.html"
                )
            )

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
