from __future__ import annotations
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from autonomy.decision.shadow_authority import AUTHORIZED_IDLE_REASON
from cli.automa_cli.decision import apply_vehicle_decision
from tests.support.cli_runner import run_automa
from tests.cli.decision.shadow_decision_surfaces_fixtures import (
    ACTIVE_RUN,
    NO_MEM_RUN,
    ShadowDecisionSurfaceFixture,
    TWO_FRAME_RUN,
)


class ShadowDecisionSurfaceTests(ShadowDecisionSurfaceFixture, unittest.TestCase):
    def test_apply_record_root_setup_failure_uses_stable_cli_error(self) -> None:
        self._stage()
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp) / "record-root-is-file"
            output_root.write_text("not a directory", encoding="utf-8")
            result = run_automa(
                "vehicles",
                "decision",
                "apply",
                "--id",
                "chase-sim-chaser",
                "--from-run",
                str(ACTIVE_RUN),
                "--record",
                "--json",
                runtime_root=self.runtime_root,
                extra_env={
                    "AUTOMA_DECISION_APPLY_OUTPUT_ROOT": str(output_root),
                },
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stderr, "")
            payload = json.loads(result.stdout)
            self.assertEqual(payload["schema"], "vehicle_decision_error_v0")
            self.assertEqual(payload["error"], "record_write_failed")

    def test_apply_record_name_collisions_preserve_preexisting_paths(self) -> None:
        self._stage()
        with tempfile.TemporaryDirectory() as out:
            out_root = Path(out)
            final_name = "chase-sim-chaser-STAMP-abc123"
            partial_name = f".{final_name}.partial"
            for collision_name in (final_name, partial_name):
                with self.subTest(collision_name=collision_name):
                    collision = out_root / collision_name
                    collision.mkdir()
                    sentinel = collision / "sentinel.txt"
                    sentinel.write_text("keep me", encoding="utf-8")
                    with (
                        patch(
                            "cli.automa_cli.decision.time.strftime",
                            return_value="STAMP",
                        ),
                        patch(
                            "cli.automa_cli.decision.secrets.token_hex",
                            return_value="abc123",
                        ),
                    ):
                        result = apply_vehicle_decision(
                            vehicle_id="chase-sim-chaser",
                            from_run=ACTIVE_RUN,
                            json_output=True,
                            record=True,
                            output_root=out_root,
                        )
                    self.assertEqual(result.exit_code, 2)
                    self.assertEqual(
                        json.loads(result.message)["error"],
                        "record_write_failed",
                    )
                    self.assertTrue(collision.is_dir())
                    self.assertEqual(
                        sentinel.read_text(encoding="utf-8"),
                        "keep me",
                    )
                    shutil.rmtree(collision)

    def test_apply_no_memory_frame(self) -> None:
        self._stage()
        result = apply_vehicle_decision(
            vehicle_id="chase-sim-chaser",
            from_run=NO_MEM_RUN,
            json_output=True,
        )
        self.assertEqual(result.exit_code, 0, result.message)
        payload = json.loads(result.message)
        frame0 = payload["digest"]["frames"][0]
        self.assertFalse(frame0["proposed_applied"])
        self.assertEqual(frame0["authorized_output"]["reason"], AUTHORIZED_IDLE_REASON)

    def test_apply_duplicate_frame_id_and_vehicle_mismatch(self) -> None:
        self._stage()
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            seq = json.loads((ACTIVE_RUN / "sequence.json").read_text())
            seq["frames"] = [seq["frames"][0], dict(seq["frames"][0])]
            (run_dir / "sequence.json").write_text(json.dumps(seq), encoding="utf-8")
            dup = apply_vehicle_decision(
                vehicle_id="chase-sim-chaser",
                from_run=run_dir,
                json_output=True,
            )
            self.assertEqual(dup.exit_code, 2)
            self.assertEqual(json.loads(dup.message)["error"], "run_invalid")

            seq2 = json.loads((ACTIVE_RUN / "sequence.json").read_text())
            seq2["vehicle_id"] = "other-vehicle"
            (run_dir / "sequence.json").write_text(json.dumps(seq2), encoding="utf-8")
            mismatch = apply_vehicle_decision(
                vehicle_id="chase-sim-chaser",
                from_run=run_dir,
                json_output=True,
            )
            self.assertEqual(mismatch.exit_code, 2)
            self.assertEqual(json.loads(mismatch.message)["error"], "run_invalid")

    def test_apply_bounds_max_frames(self) -> None:
        self._stage()
        with patch.object(self._decision_mod, "DECISION_APPLY_MAX_FRAMES", 1):
            result = apply_vehicle_decision(
                vehicle_id="chase-sim-chaser",
                from_run=TWO_FRAME_RUN,
                json_output=True,
            )
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(json.loads(result.message)["error"], "run_bounds_exceeded")

    def test_apply_record_oversize_cleans_partial(self) -> None:
        self._stage()
        with tempfile.TemporaryDirectory() as out:
            out_root = Path(out)
            with patch.object(
                self._decision_mod, "DECISION_APPLY_MAX_RECORD_BYTES", 50
            ):
                result = apply_vehicle_decision(
                    vehicle_id="chase-sim-chaser",
                    from_run=ACTIVE_RUN,
                    json_output=True,
                    record=True,
                    output_root=out_root,
                )
            self.assertEqual(result.exit_code, 2)
            self.assertEqual(
                json.loads(result.message)["error"], "record_bounds_exceeded"
            )
            # no leftover partial or final dirs
            leftovers = list(out_root.iterdir()) if out_root.exists() else []
            self.assertEqual(leftovers, [])

    def test_apply_record_result_failure_occurs_before_final_rename(self) -> None:
        self._stage()
        original_write_text = Path.write_text
        observed_final_dirs: list[Path] = []

        def fail_result(path: Path, data: str, *args, **kwargs):
            if path.name == "result.json":
                observed_final_dirs.extend(
                    item
                    for item in path.parent.parent.iterdir()
                    if item.is_dir() and not item.name.endswith(".partial")
                )
                raise OSError("injected result write failure")
            return original_write_text(path, data, *args, **kwargs)

        with tempfile.TemporaryDirectory() as out:
            out_root = Path(out)
            with patch.object(Path, "write_text", new=fail_result):
                result = apply_vehicle_decision(
                    vehicle_id="chase-sim-chaser",
                    from_run=ACTIVE_RUN,
                    json_output=True,
                    record=True,
                    output_root=out_root,
                )
            self.assertEqual(result.exit_code, 2)
            self.assertEqual(json.loads(result.message)["error"], "record_write_failed")
            self.assertEqual(observed_final_dirs, [])
            self.assertEqual(list(out_root.iterdir()), [])

    def test_apply_record_cleanup_failure_preserves_both_errors(self) -> None:
        self._stage()
        with tempfile.TemporaryDirectory() as out:
            out_root = Path(out)
            with (
                patch.object(
                    self._decision_mod,
                    "DECISION_APPLY_MAX_RECORD_BYTES",
                    50,
                ),
                patch(
                    "cli.automa_cli.decision._remove_tree_strict",
                    side_effect=OSError("injected cleanup failure"),
                ),
            ):
                result = apply_vehicle_decision(
                    vehicle_id="chase-sim-chaser",
                    from_run=ACTIVE_RUN,
                    json_output=True,
                    record=True,
                    output_root=out_root,
                )
            self.assertEqual(result.exit_code, 2)
            payload = json.loads(result.message)
            self.assertEqual(payload["error"], "record_bounds_exceeded")
            self.assertIn("Record artifacts are", payload["details"]["original_error"])
            self.assertTrue(payload["details"]["cleanup_errors"])

    def test_apply_record_measurement_failure_cleans_partial(self) -> None:
        self._stage()
        real_lstat = Path.lstat

        def flaky_lstat(path: Path, *args, **kwargs):
            if path.name == "result.json":
                raise OSError("injected measurement failure")
            return real_lstat(path, *args, **kwargs)

        with tempfile.TemporaryDirectory() as out:
            out_root = Path(out)
            with patch.object(Path, "lstat", new=flaky_lstat):
                result = apply_vehicle_decision(
                    vehicle_id="chase-sim-chaser",
                    from_run=ACTIVE_RUN,
                    json_output=True,
                    record=True,
                    output_root=out_root,
                )
            self.assertEqual(result.exit_code, 2)
            payload = json.loads(result.message)
            self.assertEqual(payload["error"], "record_write_failed")
            self.assertIn(
                "could not measure record artifact",
                payload["details"]["original_error"],
            )
            self.assertEqual(list(out_root.iterdir()), [])
