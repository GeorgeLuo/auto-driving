from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from cli.automa_cli.memory import (
    load_memory_observation_sequence,
    memory_snapshot_digest,
    replay_vehicle_memory,
)
from tests.support.cli_runner import run_automa

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "recurrence_sequence.json"


class MemoryReplayTests(unittest.TestCase):
    def test_replay_help_is_registered(self) -> None:
        result = run_automa("vehicles", "memory", "help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("replay", result.stdout)

    def test_load_sequence_fixture(self) -> None:
        frames = load_memory_observation_sequence(FIXTURE)
        self.assertEqual(len(frames), 3)
        self.assertEqual(frames[0]["frame_id"], "frame_000")
        self.assertEqual(frames[1]["observation"]["observation_id"], "obs_001")

    def test_replay_is_deterministic_with_ephemeral_implementation(self) -> None:
        first = replay_vehicle_memory(
            vehicle_id="chase-sim-chaser",
            sequence=FIXTURE,
            implementation_id="bounded_evidence",
            json_output=True,
            verify_twice=True,
        )
        second = replay_vehicle_memory(
            vehicle_id="chase-sim-chaser",
            sequence=FIXTURE,
            implementation_id="bounded_evidence",
            json_output=True,
            verify_twice=True,
        )
        self.assertEqual(first.exit_code, 0, first.message)
        self.assertEqual(second.exit_code, 0, second.message)
        payload_a = json.loads(first.message)
        payload_b = json.loads(second.message)
        self.assertEqual(payload_a["schema"], "vehicle_memory_replay_v0")
        self.assertTrue(payload_a["deterministic"])
        self.assertEqual(payload_a["digest"], payload_b["digest"])
        self.assertEqual(payload_a["frame_count"], 3)
        self.assertEqual(payload_a["final"]["implementation_id"], "bounded_evidence")
        self.assertGreaterEqual(payload_a["final"]["record_count"], 1)
        # Recurring thing id updates same slot; signal remains.
        record_ids = {item["record_id"] for item in payload_a["final"]["records"]}
        self.assertIn("thing:floor_boundary_000", record_ids)
        self.assertIn("signal:floor_visible", record_ids)
        # Last observation updated signal; thing still retained from prior frames.
        self.assertEqual(payload_a["final"]["record_count"], 2)

    def test_replay_cli_json_matches_digest(self) -> None:
        result = run_automa(
            "vehicles",
            "memory",
            "replay",
            str(FIXTURE),
            "--id",
            "chase-sim-chaser",
            "--implementation",
            "bounded_evidence",
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        recomputed = memory_snapshot_digest(payload["final"])
        self.assertEqual(payload["digest"], recomputed)

    def test_replay_uses_staged_activation_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            update = run_automa(
                "vehicles",
                "update",
                "memory",
                "--id",
                "chase-sim-chaser",
                "--implementation",
                "bounded_evidence",
                "--json",
                runtime_root=runtime_root,
            )
            self.assertEqual(update.returncode, 0, update.stdout)
            result = run_automa(
                "vehicles",
                "memory",
                "replay",
                str(FIXTURE),
                "--id",
                "chase-sim-chaser",
                "--json",
                runtime_root=runtime_root,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertTrue(payload["deterministic"])
            self.assertIn("active.json", payload["activation"])

    def test_missing_sequence_is_actionable(self) -> None:
        result = replay_vehicle_memory(
            vehicle_id="chase-sim-chaser",
            sequence="/no/such/sequence.json",
            implementation_id="bounded_evidence",
        )
        self.assertEqual(result.exit_code, 2)
        self.assertIn("Could not load observation sequence", result.message)

    def test_directory_sequence_loader(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sequence.json").write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
            frames = load_memory_observation_sequence(root)
            self.assertEqual(len(frames), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
