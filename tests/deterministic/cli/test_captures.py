"""Capture list, metadata, and one-frame reads over host and export recordings."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from PIL import Image

from autonomy.decision_cycle.activation import STEPS, activation_generation_id, step_activation
from autonomy.runtime.frame_loop import FRAME_PUBLICATION_SCHEMA
from autonomy.runtime.recording import RecordingWriter
from cli.automa_cli.hosts.runs.models import RunState
from cli.automa_cli.hosts.captures import (
    capture_frame,
    capture_image,
    capture_metadata,
    list_captures,
)
from cli.automa_cli.shared.io.processes import pid_alive
from cli.automa_cli.shared.paths import ROOT
from tests.support.runtime_fixtures import runtime_root_env

SOURCE = b"class Proto:\n    pass\n"
NAME = f"{hashlib.sha256(SOURCE).hexdigest()}.py"
DEAD_PID = 999_999
PNG = BytesIO()
Image.new("RGB", (4, 3), (9, 8, 7)).save(PNG, format="PNG")
PNG = PNG.getvalue()


def _steps() -> tuple[dict, str]:
    steps = {step: None for step in STEPS}
    steps["perception"] = step_activation(
        "perception", ["uploaded"], { "uploaded": f"{NAME}:Proto" },
        metadata={"controller_bundle": {"release": {"tree_sha256": "tree-1", "created_at_ms": 9}}},
    ).to_payload()
    generation = activation_generation_id({step: steps[step] for step in STEPS}, prefix="decision")
    return steps, generation


def _entry(run: Path, run_id: str, stamps: tuple[int, ...]) -> None:
    steps, generation = _steps()
    writer = RecordingWriter(run, host_name="chase", run_id=run_id)
    writer.store_generation(generation, steps, {NAME: SOURCE}.get)
    for index, stamp in enumerate(stamps):
        writer.commit({
            "host_name": "chase", "run_id": run_id, "generation_id": generation,
            "frame_id": f"frame-{index}", "frame_index": index, "timestamp_ms": stamp,
            "completed_at_ms": stamp + 30, "vehicle_id": "donkey",
            "timing": {"duration_ms": 30},
            "status": "ok" if index == 0 else "error",
            "error": None if index == 0 else "dropped",
            "application": None if index else {
                "applied": True, "mode": "autonomy", "reason": "go", "steering": 0.2, "throttle": 0.4,
            },
            "perception": None if index else {
                "things": [{"thing_id": "box", "kind": "obstacle"}],
                "plugin_runs": [{"plugin_id": "uploaded", "status": "ok"}],
            },
            "observation": None, "memory": None, "proposal": None, "plan": None, "action": None,
        }, PNG, ".png")


class CaptureReadTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._env = runtime_root_env(self.root)
        self._env.start()
        self.addCleanup(self._env.stop)
        self.addCleanup(self._tmp.cleanup)
        self.assertFalse(pid_alive(DEAD_PID))

    def _place(self, host: str, kind: str, name: str) -> Path:
        path = self.root / host / "bundle" / "runtime" / "automation" / kind / name
        path.mkdir(parents=True)
        return path

    def _host_record(self, automation: Path, pid: int) -> None:
        (automation / "host.json").write_text(json.dumps({
            "schema": "automa_runtime_host", "pid": pid, "host_name": "chase", "base_url": "http://127.0.0.1:9",
        }))

    def _ids(self) -> list[dict[str, object]]:
        listed = list_captures()
        self.assertEqual(listed["schema"], "automa_captures")
        return listed["captures"]

    def test_listing_keeps_one_row_and_the_host_copy_of_an_open_run(self) -> None:
        host_run = self._place("chase", "host-runs", "run-new")
        export = self._place("chase", "runs", "run-new")
        older = self._place("chase", "runs", "run-old")
        _entry(host_run, "run-new", (2_000, 2_500))
        (export / "manifest.json").write_text(json.dumps({
            "schema": "automa_recording_manifest", "run_id": "run-new", "host_name": "chase", "frames": [],
        }))
        _entry(older, "run-old", (1_000,))
        image = next((host_run / "frames").glob("*.png"))
        (host_run / "frames" / "link.png").symlink_to(image.name)
        self._arm("chase", export, pid=os.getpid(), run_dir_text=os.path.relpath(export, ROOT))

        rows = self._ids()
        self.assertEqual([row["run_id"] for row in rows], ["run-new", "run-old"])
        self.assertIn("/host-runs/run-new", rows[0]["capture"])
        self.assertNotIn("/runs/run-new", rows[0]["capture"])
        self.assertIn("/runs/run-old", rows[1]["capture"])
        self.assertEqual(rows[0]["state"], "active")
        self.assertEqual(rows[1]["state"], "closed")
        self.assertEqual(rows[0]["host"], "chase")
        self.assertEqual(rows[0]["vehicle"], "donkey")
        self.assertEqual(rows[0]["frame_count"], 2)
        self.assertEqual(rows[0]["started_at_ms"], 2_000)
        self.assertEqual(rows[0]["duration_ms"], 530)
        frames = next(item for item in capture_metadata(rows[0]["capture"])["files"] if item["name"] == "frames")
        self.assertEqual(frames["count"], 2)

    def test_a_closed_row_is_reread_once_its_recording_changes(self) -> None:
        run = self._place("chase", "runs", "run-old")
        manifest = run / "manifest.json"

        def write(frames: int) -> None:
            manifest.write_text(json.dumps({
                "schema": "automa_recording_manifest", "run_id": "run-old", "host_name": "chase",
                "frames": [{"frame_index": index, "timestamp_ms": 1_000 + index} for index in range(frames)],
            }))

        write(1)
        self.assertEqual([row["frame_count"] for row in self._ids()], [1])
        self.assertEqual([row["frame_count"] for row in self._ids()], [1])
        write(3)
        self.assertEqual([row["frame_count"] for row in self._ids()], [3])
        self.assertEqual(capture_metadata(self._ids()[0]["capture"])["frame_count"], 3)

    def _arm(self, host: str, run_dir: Path, *, pid: int, status: str = "running",
             recording: bool = True, run_dir_text: str | None = None) -> Path:
        automation = self.root / host / "bundle" / "runtime" / "automation"
        state = RunState(
            host_name=host, status=status, pid=pid, recording=recording,
            run_dir=str(run_dir) if run_dir_text is None else run_dir_text,
        )
        (automation / "state.json").write_text(json.dumps(state.to_dict()))
        return automation

    def test_a_capture_closes_when_the_run_or_its_host_stops(self) -> None:
        run = self._place("chase", "host-runs", "run-new")
        _entry(run, "run-new", (2_000,))
        capture = self._ids()[0]["capture"]
        automation = self._arm("chase", run, pid=os.getpid(), status="completed")
        self._host_record(automation, os.getpid())
        self.assertEqual(self._ids()[0]["state"], "closed")

        self._arm("chase", run, pid=os.getpid())
        self._host_record(automation, DEAD_PID)
        self.assertEqual(self._ids()[0]["state"], "closed")

        (automation / "host.json").unlink()
        self.assertEqual(self._ids()[0]["state"], "active")
        with self.assertRaisesRegex(ValueError, "an active capture is still being written"):
            capture_frame(capture, "0")
        with self.assertRaisesRegex(ValueError, "an active capture is still being written"):
            capture_image(capture, "0")

        (automation / "state.json").write_text("{")
        self.assertEqual(self._ids()[0]["state"], "closed")
        self.assertEqual(len(self._ids()), 1)

    def test_metadata_covers_entry_files_and_inline_frames(self) -> None:
        run = self._place("chase", "host-runs", "run-new")
        _entry(run, "run-new", (2_000, 2_500))
        capture = self._ids()[0]["capture"]
        meta = capture_metadata(capture)
        self.assertEqual(meta["format"], "entry")
        self.assertEqual(meta["flagged_frames"], 1)
        self.assertEqual(meta["viewable"], True)
        self.assertEqual(meta["release"]["tree_sha256"], "tree-1")
        perception = next(step for step in meta["generations"][0]["steps"] if step["step"] == "perception")
        self.assertEqual(perception["plugins"], ["uploaded"])
        self.assertEqual(meta["sources"], [{"name": NAME, "bytes": len(SOURCE)}])
        self.assertIn("manifest.jsonl", [item["name"] for item in meta["files"]])

        payload = capture_frame(capture, "0")
        self.assertEqual(payload["schema"], FRAME_PUBLICATION_SCHEMA)
        self.assertEqual(payload["health"], "healthy")
        self.assertEqual(payload["result_age_ms"], 30)
        self.assertEqual(payload["evidence"], None)
        self.assertEqual(payload["report"]["cycle"]["perception"]["things"][0]["thing_id"], "box")
        self.assertEqual(payload["report"]["application"]["steering"], 0.2)
        self.assertEqual(payload["report"]["values"]["timing"]["duration_ms"], 30)
        self.assertEqual((payload["image"]["width_px"], payload["image"]["height_px"]), (4, 3))
        self.assertIn("/api/captures/image?", payload["image"]["url"])
        self.assertEqual(capture_image(capture, "0"), (PNG, "image/png"))
        self.assertIsNone(capture_frame(capture, "1")["report"]["application"])

        inline = self._place("chase", "runs", "run-inline")
        source_name = "inline_src.py"
        (inline / "plugins").mkdir()
        (inline / "plugins" / source_name).write_bytes(b"class P:\n    pass\n")
        (inline / "frames").mkdir()
        (inline / "frames" / "f0.png").write_bytes(b"not an image")
        steps = {"perception": {"plugins": ["floor"], "metadata": {"controller_bundle": {"release": {"tree_sha256": "tree-old"}}}}}
        frames = [
            {"frame_id": "f0", "frame_index": 0, "timestamp_ms": 5_000, "image_path": "frames/f0.png",
             "generation_id": "gen-inline", "vehicle_id": "piracer", "steps": steps,
             "plugin_sources": {f"/tmp/plugins/{source_name}": f"plugins/{source_name}"},
             "perception": {"things": [], "plugin_runs": []}},
            {"frame_id": "f1", "frame_index": 1, "timestamp_ms": 6_000, "generation_id": "gen-inline",
             "absence_reason": "camera unavailable", "steps": steps},
        ]
        (inline / "manifest.json").write_text(json.dumps({
            "schema": "automa_recording_manifest", "run_id": "shown-inline", "vehicle_id": "piracer", "frames": frames,
        }))
        inline_id = next(row["capture"] for row in self._ids() if row["run_id"] == "shown-inline")
        inline_meta = capture_metadata(inline_id)
        self.assertEqual(inline_meta["format"], "inline")
        self.assertEqual(inline_meta["host"], "chase")
        self.assertEqual(inline_meta["vehicle"], "piracer")
        self.assertEqual((inline_meta["frame_count"], inline_meta["flagged_frames"]), (2, 1))
        self.assertEqual(inline_meta["sources"], [{"name": source_name, "bytes": len(b"class P:\n    pass\n")}])
        self.assertEqual(inline_meta["generations"][0]["steps"][0]["plugins"], ["floor"])
        with self.assertRaisesRegex(ValueError, "not decodable"):
            capture_frame(inline_id, "0")
        absent = capture_frame(inline_id, "1")
        self.assertIsNone(absent["image"]["url"])
        self.assertIsNone(absent["report"]["cycle"]["perception"])
        with self.assertRaisesRegex(ValueError, "this frame has no image"):
            capture_image(inline_id, "1")

    def test_paths_outside_a_recording_are_rejected(self) -> None:
        run = self._place("chase", "host-runs", "run-new")
        _entry(run, "run-new", (2_000,))
        capture = self._ids()[0]["capture"]
        alias = run.parent / "alias"
        alias.symlink_to(run.name, target_is_directory=True)
        other = self._place("chase", "captures", "extra")
        (other / "manifest.json").write_text("{}")
        refused = {
            capture + "/../../../outside": "capture must stay inside the runtime root",
            "/etc/passwd": "capture must stay inside the runtime root",
            "chase\\bundle": "capture must stay inside the runtime root",
            str(alias.relative_to(self.root)): "capture must stay inside the runtime root",
            "chase/bundle/runtime/automation/captures/extra": "capture is not a recording",
            "chase/bundle/runtime/automation/host-runs/missing": "capture is not a recording",
        }
        for path, message in refused.items():
            with self.subTest(path=path):
                with self.assertRaisesRegex(ValueError, message):
                    capture_metadata(path)
        with self.assertRaisesRegex(ValueError, "index must be a non-negative integer"):
            capture_frame(capture, "-1")
        with self.assertRaisesRegex(ValueError, "frame index is outside the capture"):
            capture_frame(capture, "4")
