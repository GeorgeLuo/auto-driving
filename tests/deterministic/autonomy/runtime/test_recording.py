"""Uploaded plugin sources a recording carries, and how replay finds them."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from autonomy.decision_cycle.activation import DECISION_STEPS, STEPS, activation_generation_id, step_activation
from autonomy.plugins import declared_plugin_id
from autonomy.runtime.plugin_loader import code_source_from_activation
from autonomy.runtime.recording import (
    RECORDING_MANIFEST_NAME,
    UPLOADED_SOURCES_DIR,
    recorded_selections,
    write_recorded_frame,
)

SOURCE = b"class Prototype:\n    plugin_id = 'uploaded'\n"
NAME = f"{hashlib.sha256(SOURCE).hexdigest()}.py"


def _steps(spec: str) -> dict:
    steps = {step: None for step in STEPS}
    steps["proposal"] = step_activation("proposal", ["uploaded"], {"uploaded": spec}).to_payload()
    return steps


def _generation(steps: dict) -> str:
    return activation_generation_id({step: steps[step] for step in DECISION_STEPS}, prefix="decision")


class RecordedSourceTests(unittest.TestCase):
    def test_a_frame_must_carry_each_uploaded_source_it_ran(self) -> None:
        steps = _steps(f"{NAME}:Prototype")

        def frame(index: int) -> dict:
            return {
                "host_name": "picar", "run_id": "run-1", "generation_id": _generation(steps),
                "frame_id": f"frame-{index}", "frame_index": index, "captured_at_ms": index,
                "step_activations": steps, "context": {"mode": "observe_only"}, "perception": None,
            }

        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            for sources in ({}, {NAME: b"other code"}):
                with self.subTest(sources=list(sources.values())):
                    with self.assertRaisesRegex(ValueError, f"ran uploaded source {NAME} without carrying it"):
                        write_recorded_frame(run, frame(0), b"image", ".png", sources)
                    self.assertFalse((run / RECORDING_MANIFEST_NAME).exists())
            write_recorded_frame(run, frame(0), b"image", ".png", {NAME: SOURCE})
            # A later frame naming the same source reuses the copy the run holds.
            write_recorded_frame(run, frame(1), b"image", ".png", {})
            self.assertEqual((run / UPLOADED_SOURCES_DIR / NAME).read_bytes(), SOURCE)
            self.assertEqual(recorded_selections({"steps": steps})["proposal"].plugin_specs,
                             {"uploaded": f"{NAME}:Prototype"})

    def test_a_moved_legacy_recording_runs_its_retained_source(self) -> None:
        original = f"/removed-host/uploads/{NAME}"
        steps = _steps(f"{original}:Prototype")
        record = {"steps": steps, "generation_id": _generation(steps),
                  "plugin_sources": {original: f"{UPLOADED_SOURCES_DIR}/{NAME}"}}
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "moved"
            retained = run / UPLOADED_SOURCES_DIR / NAME
            retained.parent.mkdir(parents=True)
            retained.write_bytes(SOURCE)

            proposal = recorded_selections(record, source_root=run)["proposal"]

            # The recorded identity is kept; only the code to run is redirected.
            self.assertEqual(proposal.plugin_specs, {"uploaded": f"{original}:Prototype"})
            executable = code_source_from_activation(proposal).executable_activation(proposal)
            spec = executable.plugin_specs["uploaded"]
            self.assertEqual(spec, f"{retained.resolve()}:Prototype")
            self.assertEqual(declared_plugin_id(spec), "uploaded")
            escaped = {**record, "plugin_sources": {original: "../outside.py"}}
            with self.assertRaisesRegex(ValueError, "must stay inside the run directory"):
                recorded_selections(escaped, source_root=run)


if __name__ == "__main__":
    unittest.main()
