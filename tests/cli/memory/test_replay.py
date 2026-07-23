from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from cli.automa_cli.memory import (
    MEMORY_REPLAY_MAX_FRAMES,
    MEMORY_REPLAY_RECORD_ARTIFACTS,
    _directory_byte_size,
    load_memory_observation_sequence,
    memory_snapshot_digest,
    replay_vehicle_memory,
    write_memory_replay_record,
)
from tests.support.cli_runner import run_automa

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "recurrence_sequence.json"


class MemoryReplayTests(unittest.TestCase):
    def test_replay_help_is_registered(self) -> None:
        result = run_automa("vehicles", "memory", "help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("replay", result.stdout)

    def test_replay_record_flag_in_help(self) -> None:
        result = run_automa("vehicles", "memory", "replay", "--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("--record", result.stdout)

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
        self.assertIn("thing:1:14:floor-plane-v0:18:floor_boundary_000", record_ids)
        self.assertIn("signal:1:20:lightweight_observer:13:floor_visible", record_ids)
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

    def test_replay_without_record_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp) / "memory-replay"
            result = replay_vehicle_memory(
                vehicle_id="chase-sim-chaser",
                sequence=FIXTURE,
                implementation_id="bounded_evidence",
                json_output=True,
                record=False,
                output_root=output_root,
            )
            self.assertEqual(result.exit_code, 0, result.message)
            payload = json.loads(result.message)
            self.assertFalse(payload["recorded"])
            self.assertIsNone(payload["record_dir"])
            self.assertFalse(output_root.exists())

    def test_replay_record_writes_bounded_provenance_extract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp) / "memory-replay"
            result = replay_vehicle_memory(
                vehicle_id="chase-sim-chaser",
                sequence=FIXTURE,
                implementation_id="bounded_evidence",
                json_output=True,
                record=True,
                output_root=output_root,
            )
            self.assertEqual(result.exit_code, 0, result.message)
            payload = json.loads(result.message)
            self.assertTrue(payload["recorded"])
            self.assertIsNotNone(payload["record_dir"])
            self.assertIsNotNone(payload["provenance_extract"])

            # Resolve record dir from payload display path or output_root children.
            run_dirs = list(output_root.iterdir())
            self.assertEqual(len(run_dirs), 1)
            record_dir = run_dirs[0]
            for name in MEMORY_REPLAY_RECORD_ARTIFACTS:
                self.assertTrue((record_dir / name).is_file(), name)

            manifest = json.loads((record_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["schema"], "automa_memory_replay_record_v0")
            self.assertTrue(manifest["opt_in"])
            self.assertFalse(manifest["writes_default_history"])
            self.assertFalse(manifest["bounds"]["includes_raw_camera_images"])
            self.assertEqual(
                manifest["bounds"]["retained_evidence_labeled_as"],
                "retained_not_current",
            )
            self.assertEqual(manifest["bounds"]["max_frames"], MEMORY_REPLAY_MAX_FRAMES)
            self.assertIn("bytes_in_record", manifest["bounds"])
            self.assertGreater(manifest["bounds"]["bytes_in_record"], 0)

            extract = (record_dir / "provenance_extract.html").read_text(encoding="utf-8")
            self.assertIn("retained evidence", extract.lower())
            self.assertIn("not current camera geometry", extract.lower())
            self.assertIn("thing:1:14:floor-plane-v0:18:floor_boundary_000", extract)
            self.assertIn("provenance.frame_id", extract)
            self.assertIn("obs_001", extract)  # last update of recurring thing

            result_on_disk = json.loads((record_dir / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(result_on_disk["digest"], payload["digest"])
            self.assertGreaterEqual(len(result_on_disk["provenance_rows"]), 1)
            for row in result_on_disk["provenance_rows"]:
                self.assertTrue(row["retained_not_current"])
                self.assertIn("provenance", row)

    def test_cli_record_flag_end_to_end(self) -> None:
        import os

        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp) / "memory-replay"
            env_key = "AUTOMA_MEMORY_REPLAY_OUTPUT_ROOT"
            previous = os.environ.get(env_key)
            os.environ[env_key] = str(output_root)
            try:
                result = run_automa(
                    "vehicles",
                    "memory",
                    "replay",
                    str(FIXTURE),
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
            self.assertEqual(len(list(output_root.iterdir())), 1)

    def test_replay_rejects_sequences_over_frame_ceiling(self) -> None:
        frames = [
            {
                "frame_id": f"frame_{index:04d}",
                "frame_index": index,
                "timestamp_ms": 1_000 + index,
                "observation": {
                    "observation_id": f"obs_{index:04d}",
                    "created_at_ms": 1_000 + index,
                    "sensor_snapshot": {},
                    "perception_plugin_id": "lightweight_observer",
                    "summary": [f"frame {index}"],
                    "things": [],
                    "signals": [],
                },
            }
            for index in range(MEMORY_REPLAY_MAX_FRAMES + 1)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            sequence = Path(tmp) / "too_long.json"
            sequence.write_text(
                json.dumps({"schema": "automa_memory_observation_sequence_v0", "frames": frames}),
                encoding="utf-8",
            )
            result = replay_vehicle_memory(
                vehicle_id="chase-sim-chaser",
                sequence=sequence,
                implementation_id="bounded_evidence",
                json_output=True,
            )
        self.assertEqual(result.exit_code, 2)
        self.assertIn("max allowed", result.message)

    def test_loader_enforces_max_frames_before_return(self) -> None:
        frames = [
            {
                "frame_id": f"frame_{index:04d}",
                "frame_index": index,
                "timestamp_ms": 1_000 + index,
                "observation": {
                    "observation_id": f"obs_{index:04d}",
                    "created_at_ms": 1_000 + index,
                    "sensor_snapshot": {},
                    "perception_plugin_id": "lightweight_observer",
                    "summary": [f"frame {index}"],
                    "things": [],
                    "signals": [],
                },
            }
            for index in range(5)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            sequence = Path(tmp) / "five.json"
            sequence.write_text(
                json.dumps(
                    {"schema": "automa_memory_observation_sequence_v0", "frames": frames}
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "max allowed is 3"):
                load_memory_observation_sequence(sequence, max_frames=3)

    def test_loader_enforces_sequence_file_byte_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sequence = Path(tmp) / "bulky.json"
            sequence.write_text("{" + ("x" * 200) + "}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "max allowed is 64"):
                load_memory_observation_sequence(
                    sequence,
                    max_frames=16,
                    max_sequence_file_bytes=64,
                )

    def test_loader_directory_rejects_excess_frame_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index in range(4):
                (root / f"frame_{index:03d}.json").write_text(
                    json.dumps(
                        {
                            "frame_id": f"frame_{index:03d}",
                            "frame_index": index,
                            "timestamp_ms": index,
                            "observation": {
                                "observation_id": f"obs_{index:03d}",
                                "created_at_ms": index,
                                "sensor_snapshot": {},
                                "perception_plugin_id": "lightweight_observer",
                                "summary": [],
                                "things": [],
                                "signals": [],
                            },
                        }
                    ),
                    encoding="utf-8",
                )
            with self.assertRaisesRegex(ValueError, "frame files"):
                load_memory_observation_sequence(root, max_frames=3)

    def test_record_enforces_total_byte_ceiling(self) -> None:
        frames = load_memory_observation_sequence(FIXTURE)
        payload = {
            "schema": "vehicle_memory_replay_v0",
            "vehicle_id": "chase-sim-chaser",
            "frame_count": len(frames),
            "implementation_id": "bounded_evidence",
            "digest": "abc",
            "final": {
                "health": "healthy",
                "record_count": 1,
                "records": [
                    {
                        "record_id": "thing:1:14:floor-plane-v0:18:floor_boundary_000",
                        "kind": "floor_boundary",
                        "label": "boundary",
                        "confidence": 0.9,
                        "provenance": {
                            "frame_id": "frame_001",
                            "observation_id": "obs_001",
                            "evidence_id": "floor_boundary_000",
                        },
                    }
                ],
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp) / "memory-replay"
            with self.assertRaisesRegex(ValueError, "max_record_bytes"):
                write_memory_replay_record(
                    vehicle_id="chase-sim-chaser",
                    sequence_path=FIXTURE,
                    frames=frames,
                    payload=payload,
                    output_root=output_root,
                    max_frames=16,
                    max_record_bytes=200,
                )
            # Fail closed: no partial record directory left behind.
            self.assertFalse(output_root.exists() and any(output_root.iterdir()))

    def test_record_stabilizes_bytes_in_record_on_disk(self) -> None:
        frames = load_memory_observation_sequence(FIXTURE)
        payload = {
            "schema": "vehicle_memory_replay_v0",
            "vehicle_id": "chase-sim-chaser",
            "frame_count": len(frames),
            "implementation_id": "bounded_evidence",
            "digest": "abc",
            "final": {
                "health": "healthy",
                "record_count": 1,
                "records": [
                    {
                        "record_id": "thing:1:14:floor-plane-v0:18:floor_boundary_000",
                        "kind": "floor_boundary",
                        "label": "boundary",
                        "confidence": 0.9,
                        "provenance": {
                            "frame_id": "frame_001",
                            "observation_id": "obs_001",
                            "evidence_id": "floor_boundary_000",
                        },
                    }
                ],
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp) / "memory-replay"
            written = write_memory_replay_record(
                vehicle_id="chase-sim-chaser",
                sequence_path=FIXTURE,
                frames=frames,
                payload=payload,
                output_root=output_root,
                max_frames=16,
                max_record_bytes=2 * 1024 * 1024,
            )
            record_dir = output_root / Path(written["record_dir"]).name
            on_disk = _directory_byte_size(record_dir)
            manifest = json.loads((record_dir / "manifest.json").read_text(encoding="utf-8"))
            result_on_disk = json.loads((record_dir / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["bounds"]["bytes_in_record"], on_disk)
            self.assertEqual(
                result_on_disk["record_manifest"]["bounds"]["bytes_in_record"],
                on_disk,
            )
            self.assertEqual(result_on_disk["record_bounds"]["bytes_in_record"], on_disk)


if __name__ == "__main__":
    unittest.main(verbosity=2)
