from __future__ import annotations
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from cli.automa_cli.memory import (
    _directory_byte_size,
    _remove_tree_strict,
    _stabilize_record_byte_count,
    load_memory_observation_sequence,
    write_memory_replay_record,
)
from tests.cli.memory.replay_fixtures import (
    RECURRENCE_SOURCE,
    MemoryReplayFixture,
)


class MemoryReplayTests(MemoryReplayFixture, unittest.TestCase):
    def test_directory_byte_size_fails_closed_on_stat_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "sequence.json"
            target.write_text("{}", encoding="utf-8")
            real_lstat = Path.lstat

            def flaky_lstat(self: Path, *args, **kwargs):
                if self.name == "sequence.json":
                    raise OSError("injected stat failure")
                return real_lstat(self, *args, **kwargs)

            with patch.object(Path, "lstat", flaky_lstat):
                with self.assertRaisesRegex(
                    OSError, "could not measure record artifact"
                ):
                    _directory_byte_size(root)

    def test_record_aborts_when_artifact_cannot_be_measured(self) -> None:
        frames = load_memory_observation_sequence(RECURRENCE_SOURCE)
        payload = {
            "schema": "vehicle_memory_replay_v0",
            "vehicle_id": "chase-sim-chaser",
            "frame_count": len(frames),
            "implementation_id": "bounded_evidence",
            "digest": "abc",
            "final": {"health": "healthy", "record_count": 0, "records": []},
        }
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp) / "memory-replay"
            real_lstat = Path.lstat

            def flaky_lstat(self: Path, *args, **kwargs):
                if self.name == "sequence.json":
                    raise OSError("injected stat failure")
                return real_lstat(self, *args, **kwargs)

            with patch.object(Path, "lstat", flaky_lstat):
                with self.assertRaisesRegex(OSError, "could not measure"):
                    write_memory_replay_record(
                        vehicle_id="chase-sim-chaser",
                        sequence_path=RECURRENCE_SOURCE,
                        frames=frames,
                        payload=payload,
                        output_root=output_root,
                        max_frames=16,
                        max_record_bytes=2 * 1024 * 1024,
                    )
            self.assertFalse(output_root.exists() and any(output_root.iterdir()))

    def test_cleanup_failure_surfaces_residual_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            record_dir = Path(tmp) / "partial-record"
            record_dir.mkdir()
            sticky = record_dir / "sequence.json"
            sticky.write_text("{}", encoding="utf-8")
            real_unlink = Path.unlink

            def deny_unlink(self: Path, *args, **kwargs):
                if self.name == "sequence.json":
                    raise PermissionError("denied")
                return real_unlink(self, *args, **kwargs)

            with patch.object(Path, "unlink", deny_unlink):
                with self.assertRaisesRegex(
                    OSError, "failed to remove partial record tree"
                ) as ctx:
                    _remove_tree_strict(record_dir)
            self.assertTrue(sticky.exists())
            self.assertIn(str(record_dir), str(ctx.exception))
            self.assertIn("sequence.json", str(ctx.exception))

    def test_stabilize_converges_across_digit_boundary(self) -> None:
        """Declared size must converge when digit width grows (e.g. 999 -> 1001)."""

        with tempfile.TemporaryDirectory() as tmp:
            record_dir = Path(tmp) / "record"
            record_dir.mkdir()
            (record_dir / "payload.bin").write_bytes(b"x" * 10)
            extract_path = record_dir / "provenance_extract.html"
            extract_path.write_text("<html></html>", encoding="utf-8")
            manifest: dict = {"bounds": {}}
            # measure → write → measure (grew) → measure → write → measure (stable)
            sizes = [999, 1001, 1001, 1001]
            with patch("cli.automa_cli.memory._directory_byte_size", side_effect=sizes):
                total = _stabilize_record_byte_count(
                    record_dir=record_dir,
                    manifest=manifest,
                    payload={"schema": "vehicle_memory_replay_v0", "digest": "x"},
                    extract_path=extract_path,
                    provenance_rows=[],
                    max_record_bytes=10_000,
                )
            self.assertEqual(total, 1001)
            self.assertEqual(manifest["bounds"]["bytes_in_record"], 1001)
            result = json.loads(
                (record_dir / "result.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                result["record_manifest"]["bounds"]["bytes_in_record"], 1001
            )

    def test_record_enforces_total_byte_ceiling(self) -> None:
        frames = load_memory_observation_sequence(RECURRENCE_SOURCE)
        payload = self._record_payload(frames)
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp) / "memory-replay"
            with self.assertRaisesRegex(ValueError, "max_record_bytes"):
                write_memory_replay_record(
                    vehicle_id="chase-sim-chaser",
                    sequence_path=RECURRENCE_SOURCE,
                    frames=frames,
                    payload=payload,
                    output_root=output_root,
                    max_frames=16,
                    max_record_bytes=200,
                )
            # Fail closed: no partial record directory left behind.
            self.assertFalse(output_root.exists() and any(output_root.iterdir()))

    def test_record_byte_ceiling_below_exact_and_above(self) -> None:
        """Configured max_record_bytes is the acceptance boundary (no fixed reservation)."""

        frames = load_memory_observation_sequence(RECURRENCE_SOURCE)
        payload = self._record_payload(frames)
        frozen_stamp = "20200101-000000"
        frozen_time = 1_577_836_800.0

        def clear_output(root: Path) -> None:
            if root.exists():
                for child in list(root.iterdir()):
                    _remove_tree_strict(child)

        def write_with(root: Path, ceiling: int) -> dict:
            return write_memory_replay_record(
                vehicle_id="chase-sim-chaser",
                sequence_path=RECURRENCE_SOURCE,
                frames=frames,
                payload=payload,
                output_root=root,
                max_frames=16,
                max_record_bytes=ceiling,
            )

        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp) / "memory-replay"
            with patch(
                "cli.automa_cli.memory.time.strftime", return_value=frozen_stamp
            ), patch("cli.automa_cli.memory.time.time", return_value=frozen_time):
                # Generous ceiling succeeds (above any tight bound).
                generous = write_with(output_root, 2 * 1024 * 1024)
                measured = int(generous["manifest"]["bounds"]["bytes_in_record"])
                self.assertGreater(measured, 1_000)

                # Re-measure under a ceiling with the same digit width as the final
                # size so max_record_bytes encoding does not change the total.
                clear_output(output_root)
                tight = write_with(output_root, measured)
                exact = int(tight["manifest"]["bounds"]["bytes_in_record"])
                self.assertLessEqual(exact, measured)

                # Exact boundary: same digit-width ceiling equals on-disk total.
                clear_output(output_root)
                at_limit = write_with(output_root, exact)
                self.assertEqual(
                    at_limit["manifest"]["bounds"]["bytes_in_record"], exact
                )
                record_dir = output_root / Path(at_limit["record_dir"]).name
                self.assertEqual(_directory_byte_size(record_dir), exact)

                # Above boundary: also succeeds.
                clear_output(output_root)
                above = write_with(output_root, exact + 128)
                self.assertEqual(above["manifest"]["bounds"]["bytes_in_record"], exact)

                # Below boundary: rejected and cleaned up.
                clear_output(output_root)
                with self.assertRaisesRegex(ValueError, "max_record_bytes"):
                    write_with(output_root, exact - 1)
                self.assertFalse(output_root.exists() and any(output_root.iterdir()))

    def test_record_stabilizes_bytes_in_record_on_disk(self) -> None:
        frames = load_memory_observation_sequence(RECURRENCE_SOURCE)
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
                sequence_path=RECURRENCE_SOURCE,
                frames=frames,
                payload=payload,
                output_root=output_root,
                max_frames=16,
                max_record_bytes=2 * 1024 * 1024,
            )
            record_dir = output_root / Path(written["record_dir"]).name
            on_disk = _directory_byte_size(record_dir)
            manifest = json.loads(
                (record_dir / "manifest.json").read_text(encoding="utf-8")
            )
            result_on_disk = json.loads(
                (record_dir / "result.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["bounds"]["bytes_in_record"], on_disk)
            self.assertEqual(
                result_on_disk["record_manifest"]["bounds"]["bytes_in_record"],
                on_disk,
            )
            self.assertEqual(
                result_on_disk["record_bounds"]["bytes_in_record"], on_disk
            )
