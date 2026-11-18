"""Generations and uploaded plugin sources a recording stores once."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from autonomy.decision_cycle.activation import STEPS, activation_generation_id, step_activation
from autonomy.runtime.recording import (
    RECORDING_MANIFEST_NAME,
    UPLOADED_SOURCES_DIR,
    RecordingWriter,
    recorded_selections,
)

SOURCE = b"class Prototype:\n    plugin_id = 'uploaded'\n"
NAME = f"{hashlib.sha256(SOURCE).hexdigest()}.py"


def _steps(spec: str) -> dict:
    steps = {step: None for step in STEPS}
    steps["proposal"] = step_activation("proposal", ["uploaded"], {"uploaded": spec}).to_payload()
    return steps


def _generation(steps: dict) -> str:
    return activation_generation_id({step: steps[step] for step in STEPS}, prefix="decision")


class RecordedGenerationTests(unittest.TestCase):
    def test_a_generation_must_carry_each_uploaded_source_it_ran_before_a_frame_names_it(self) -> None:
        steps = _steps(f"{NAME}:Prototype")
        generation_id = _generation(steps)

        def frame(index: int) -> dict:
            return {
                "host_name": "picar", "run_id": "run-1", "generation_id": generation_id,
                "frame_id": f"frame-{index}", "frame_index": index, "timestamp_ms": index,
            }

        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            writer = RecordingWriter(run, host_name="picar", run_id="run-1")
            for sources in ({}, {NAME: b"other code"}):
                with self.subTest(sources=list(sources.values())):
                    with self.assertRaisesRegex(ValueError, f"ran uploaded source {NAME} without carrying it"):
                        writer.store_generation(generation_id, steps, sources.get)
                    with self.assertRaisesRegex(ValueError, "names a generation the run has not stored"):
                        writer.commit(frame(0), b"image", ".png")
                    self.assertFalse((run / RECORDING_MANIFEST_NAME).exists())
            with self.assertRaisesRegex(ValueError, "recorded generation does not match its step selections"):
                writer.store_generation(_generation(_steps("other.py:Prototype")), steps, {NAME: SOURCE}.get)
            writer.store_generation(generation_id, steps, {NAME: SOURCE}.get)
            # A later frame of the generation reuses the copies the run holds.
            writer.store_generation(generation_id, steps, {}.get)
            for index in range(2):
                writer.commit(frame(index), b"image", ".png")
            self.assertEqual((run / UPLOADED_SOURCES_DIR / NAME).read_bytes(), SOURCE)
            self.assertEqual(recorded_selections({"steps": steps})["proposal"].plugin_specs,
                             {"uploaded": f"{NAME}:Prototype"})


if __name__ == "__main__":
    unittest.main()
