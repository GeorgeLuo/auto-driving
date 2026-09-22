from __future__ import annotations
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from autonomy.decision.memory import canonical_json_bytes, canonical_json_utf8
from autonomy.decision.shadow_authority import AUTHORIZED_IDLE_REASON
from cli.automa_cli.decision import ENGINE_ID, apply_vehicle_decision
from tests.support.cli_runner import run_automa
from tests.cli.decision.shadow_decision_surfaces_fixtures import (
    ACTIVE_RUN,
    NO_MEM_RUN,
    ShadowDecisionSurfaceFixture,
)


class ShadowDecisionSurfaceTests(ShadowDecisionSurfaceFixture, unittest.TestCase):
    def test_apply_requires_id_and_shadow_engine(self) -> None:
        missing = apply_vehicle_decision(
            vehicle_id=None,
            from_run=ACTIVE_RUN,
            json_output=True,
        )
        self.assertEqual(missing.exit_code, 2)
        self.assertEqual(json.loads(missing.message)["error"], "missing_vehicle_id")

        self._stage(engine_id="idle")
        wrong = apply_vehicle_decision(
            vehicle_id="chase-sim-chaser",
            from_run=ACTIVE_RUN,
            json_output=True,
        )
        self.assertEqual(wrong.exit_code, 2)
        self.assertEqual(json.loads(wrong.message)["error"], "wrong_engine")

    def test_apply_digest_determinism_byte_equality(self) -> None:
        self._stage()
        first = apply_vehicle_decision(
            vehicle_id="chase-sim-chaser",
            from_run=ACTIVE_RUN,
            json_output=True,
        )
        second = apply_vehicle_decision(
            vehicle_id="chase-sim-chaser",
            from_run=ACTIVE_RUN,
            json_output=True,
        )
        self.assertEqual(first.exit_code, 0, first.message)
        self.assertEqual(second.exit_code, 0, second.message)
        a = json.loads(first.message)
        b = json.loads(second.message)
        self.assertTrue(a["deterministic"])
        self.assertEqual(a["digest_sha256"], b["digest_sha256"])
        self.assertEqual(
            canonical_json_utf8(a["digest"]),
            canonical_json_utf8(b["digest"]),
        )
        # length-only equality is not the success criterion used
        self.assertEqual(
            canonical_json_bytes(a["digest"]),
            len(canonical_json_utf8(a["digest"])),
        )
        self.assertFalse(a["recorded"])
        self.assertIsNone(a["record_dir"])
        frame0 = a["digest"]["frames"][0]
        self.assertFalse(frame0["proposed_applied"])
        self.assertEqual(
            frame0["authorized_output"]["reason"],
            AUTHORIZED_IDLE_REASON,
        )
        self.assertIsNotNone(frame0["proposed"])
        self.assertNotEqual(frame0["proposed"]["steering"], 0.0)

    def test_apply_default_writes_nothing_record_writes_html(self) -> None:
        self._stage()
        with tempfile.TemporaryDirectory() as out:
            out_root = Path(out)
            before = list(out_root.iterdir()) if out_root.exists() else []
            default = apply_vehicle_decision(
                vehicle_id="chase-sim-chaser",
                from_run=ACTIVE_RUN,
                json_output=True,
                record=False,
                output_root=out_root,
            )
            self.assertEqual(default.exit_code, 0, default.message)
            self.assertEqual(
                list(out_root.iterdir()) if out_root.exists() else [], before
            )

            recorded = apply_vehicle_decision(
                vehicle_id="chase-sim-chaser",
                from_run=ACTIVE_RUN,
                json_output=True,
                record=True,
                output_root=out_root,
            )
            self.assertEqual(recorded.exit_code, 0, recorded.message)
            payload = json.loads(recorded.message)
            self.assertTrue(payload["recorded"])
            record_dir = Path(payload["record_dir"])
            # display_path may be relative; resolve via out_root children
            dirs = [p for p in out_root.iterdir() if p.is_dir()]
            self.assertEqual(len(dirs), 1)
            record_dir = dirs[0]
            self.assertTrue((record_dir / "manifest.json").exists())
            self.assertTrue((record_dir / "digest.json").exists())
            self.assertTrue((record_dir / "result.json").exists())
            html_files = list((record_dir / "frames").glob("*.html"))
            self.assertEqual(len(html_files), 1)
            html_text = html_files[0].read_text(encoding="utf-8")
            self.assertIn("proposed_applied=false", html_text)
            self.assertIn("memory_record", html_text)
            self.assertIn(
                "contribution_plugins=avoid_recent_obstruction",
                html_text,
            )

            before_idle = set(out_root.iterdir())
            idle_recorded = apply_vehicle_decision(
                vehicle_id="chase-sim-chaser",
                from_run=NO_MEM_RUN,
                json_output=True,
                record=True,
                output_root=out_root,
            )
            self.assertEqual(idle_recorded.exit_code, 0, idle_recorded.message)
            idle_dir = next(iter(set(out_root.iterdir()) - before_idle))
            idle_html = next((idle_dir / "frames").glob("*.html")).read_text(
                encoding="utf-8"
            )
            self.assertIn("status=idle", idle_html)
            self.assertIn("contribution_plugins=(none)", idle_html)

    def test_apply_record_source_image_paths_and_symlink_rejection(self) -> None:
        self._stage()
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as out:
            run_dir = Path(tmp) / "run"
            shutil.copytree(ACTIVE_RUN, run_dir)
            source_frames = run_dir / "frames"
            source_frames.mkdir()
            source_image = source_frames / "frame_001.png"
            source_image.write_bytes(b"\x89PNG\r\n\x1a\nfixture")

            recorded = apply_vehicle_decision(
                vehicle_id="chase-sim-chaser",
                from_run=run_dir,
                json_output=True,
                record=True,
                output_root=Path(out),
            )
            self.assertEqual(recorded.exit_code, 0, recorded.message)
            record_dir = next(Path(out).iterdir())
            manifest = json.loads((record_dir / "manifest.json").read_text())
            self.assertEqual(
                manifest["frames"][0]["source_image"],
                "source_frames/frame_001.png",
            )
            html_path = record_dir / manifest["frames"][0]["html"]
            html_text = html_path.read_text(encoding="utf-8")
            match = re.search(r'<img src="([^"]+)"', html_text)
            self.assertIsNotNone(match)
            self.assertEqual(match.group(1), "../source_frames/frame_001.png")
            self.assertTrue((html_path.parent / match.group(1)).resolve().is_file())

        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as out:
            run_dir = Path(tmp) / "run"
            shutil.copytree(ACTIVE_RUN, run_dir)
            source_frames = run_dir / "frames"
            source_frames.mkdir()
            sibling = source_frames / "sibling.png"
            sibling.write_bytes(b"fixture")
            (source_frames / "frame_001.png").symlink_to(sibling.name)
            rejected = apply_vehicle_decision(
                vehicle_id="chase-sim-chaser",
                from_run=run_dir,
                json_output=True,
                record=True,
                output_root=Path(out),
            )
            self.assertEqual(rejected.exit_code, 2)
            self.assertEqual(json.loads(rejected.message)["error"], "run_invalid")
            self.assertEqual(list(Path(out).iterdir()), [])

        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as out:
            temp_root = Path(tmp)
            real_run = temp_root / "real-run"
            shutil.copytree(ACTIVE_RUN, real_run)
            source_frames = real_run / "frames"
            source_frames.mkdir()
            (source_frames / "frame_001.png").write_bytes(b"fixture")
            linked_run = temp_root / "linked-run"
            linked_run.symlink_to(real_run, target_is_directory=True)
            rejected_root = apply_vehicle_decision(
                vehicle_id="chase-sim-chaser",
                from_run=linked_run,
                json_output=True,
                record=True,
                output_root=Path(out),
            )
            self.assertEqual(rejected_root.exit_code, 2)
            self.assertEqual(
                json.loads(rejected_root.message)["error"],
                "run_invalid",
            )
            self.assertEqual(list(Path(out).iterdir()), [])

        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as out:
            temp_root = Path(tmp)
            real_parent = temp_root / "real-parent"
            real_run = real_parent / "run"
            shutil.copytree(ACTIVE_RUN, real_run)
            source_frames = real_run / "frames"
            source_frames.mkdir()
            (source_frames / "frame_001.png").write_bytes(b"fixture")
            linked_parent = temp_root / "linked-parent"
            linked_parent.symlink_to(real_parent, target_is_directory=True)
            nested_under_link = linked_parent / "run"
            self.assertFalse(nested_under_link.is_symlink())
            rejected_ancestor = apply_vehicle_decision(
                vehicle_id="chase-sim-chaser",
                from_run=nested_under_link,
                json_output=True,
                record=True,
                output_root=Path(out),
            )
            self.assertEqual(rejected_ancestor.exit_code, 2)
            self.assertEqual(
                json.loads(rejected_ancestor.message)["error"],
                "run_invalid",
            )
            self.assertEqual(list(Path(out).iterdir()), [])

    def test_apply_cli_json(self) -> None:
        self._stage()
        missing_id = run_automa(
            "vehicles",
            "decision",
            "apply",
            "--from-run",
            str(ACTIVE_RUN),
            "--json",
            runtime_root=self.runtime_root,
            check=False,
        )
        self.assertEqual(missing_id.returncode, 2)
        self.assertEqual(json.loads(missing_id.stdout)["error"], "missing_vehicle_id")
        self.assertEqual(missing_id.stderr, "")

        result = run_automa(
            "vehicles",
            "decision",
            "apply",
            "--id",
            "chase-sim-chaser",
            "--from-run",
            str(ACTIVE_RUN),
            "--json",
            runtime_root=self.runtime_root,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["schema"], "vehicle_decision_apply_result_v0")
        self.assertEqual(payload["engine_id"], ENGINE_ID)
