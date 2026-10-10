"""Replay an image source through a prefix of the cycle's steps, offline.

Memory, proposal, plan and action inspect all replay a source this way: the
source's recorded selections, restaged per frame, unless overridden; then a
report of the inspected step after every frame, optionally recorded as a run
that replays as a source again.
"""

from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from autonomy.decision_cycle.activation import StepActivation
from autonomy.decision_cycle.steps import builtin_activation, step_runner
from implementations.decision_cycle.catalog import (
    DEFAULT_STEP_PRESETS, packaged_activation, selection_activation,
)

from .inspection_runs import recorded_selection, recorded_selections, replay_step, selection_record
from .paths import safe_path_part
from .workbench_frames import FrameOutcome, run_frame
from .workbench_source import (
    ImageSource,
    ReplayFrame,
    SourceValidationError,
    normalize_image_directory,
    normalize_image_file,
    read_image_manifest,
)

# A recording that disabled one of these steps replays it with no plugins.
_EMPTY_WHEN_DISABLED = ("perception", "memory")


def read_replay_source(source: str | Path, *, max_frames: int) -> tuple[ImageSource, dict[str, Any]]:
    """The source's frames and its manifest (empty for plain images).

    Raises SourceValidationError naming what is wrong with the source.
    """

    path = Path(source).expanduser()
    if max_frames <= 0:
        raise SourceValidationError("max_frames must be greater than zero")
    if path.is_file():
        return normalize_image_file(path), {}
    image_source = normalize_image_directory(path, max_frames=max_frames)
    _manifest_path, manifest = read_image_manifest(image_source.source_path)
    return image_source, manifest or {}


def default_selection(step: str) -> StepActivation | None:
    """The selection inspect uses for a step its source did not record.

    That is the step's default preset, else the built-in a vehicle runs when
    the step is unstaged, else the step's default packaged plugins.
    """

    if step in DEFAULT_STEP_PRESETS:
        return selection_activation(step)
    return builtin_activation(step) or packaged_activation(step)


class StepReplay:
    """Step runners that replay a source with the selections it recorded.

    Each step uses its override, else the selection the source recorded, else
    ``default_selection``. A frame that recorded a restage replaces that step's
    selection from then on, unless the step is overridden. Constructing the
    runners raises before any frame runs.
    """

    def __init__(
        self,
        manifest: dict[str, Any],
        *,
        steps: tuple[str, ...],
        overrides: dict[str, StepActivation] | None = None,
    ) -> None:
        self.overrides = dict(overrides or {})
        recorded = recorded_selections(manifest)
        self.activations: dict[str, StepActivation | None] = {}
        for step in steps:
            if step in self.overrides:
                self.activations[step] = self.overrides[step]
            elif step in recorded:
                self.activations[step] = _restored(step, recorded[step])
            else:
                self.activations[step] = recorded_selection(step, manifest) or default_selection(step)
        self.runners = {
            step: step_runner(activation) if activation is not None else None
            for step, activation in self.activations.items()
        }
        self.shared_memory: dict[str, Any] = {}

    def run(self, frame: ReplayFrame) -> FrameOutcome:
        selections = recorded_selections(frame.metadata)
        for step in self.activations:
            if step not in selections or step in self.overrides:
                continue
            activation = _restored(step, selections[step])
            self.runners[step] = replay_step(self.runners[step], activation, self.shared_memory)
            self.activations[step] = activation
        return run_frame(frame, steps=self.runners, shared_memory=self.shared_memory)

    def step_payloads(self) -> dict[str, Any]:
        """Each step's current selection, as a frame records it for replay."""

        return {
            step: activation.to_payload() if activation is not None else None
            for step, activation in self.activations.items()
        }

    def selection_records(self) -> dict[str, Any]:
        """Each step's current selection, as a report records it for replay."""

        return {
            step: {**selection_record(activation), "plugins": list(activation.plugins)}
            if activation is not None else None
            for step, activation in self.activations.items()
        }


def _restored(step: str, activation: StepActivation | None) -> StepActivation | None:
    if activation is None and step in _EMPTY_WHEN_DISABLED:
        return selection_activation(step, plugins=[])
    return activation


def inspect_run_id(source_id: str) -> str:
    stamp = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    return f"inspect-{safe_path_part(source_id)}-{stamp}"


def record_inspect_run(run_dir: Path, report: dict[str, Any], image_source: ImageSource) -> None:
    """Save the report with copies of its frames, so the run replays as a source.

    Raises OSError when the directory exists or cannot be written.
    """

    run_dir.mkdir(parents=True, exist_ok=False)
    report["run_dir"] = str(run_dir)
    (run_dir / "frames").mkdir()
    sources = {frame.frame_id: frame for frame in image_source.frames}
    for recorded_frame in report["frames"]:
        frame = sources[recorded_frame["frame_id"]]
        if frame.image_path is not None:
            relative = Path("frames") / f"frame_{frame.position:06d}{frame.image_path.suffix.lower()}"
            shutil.copyfile(frame.image_path, run_dir / relative)
            recorded_frame["image_path"] = relative.as_posix()
    (run_dir / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
