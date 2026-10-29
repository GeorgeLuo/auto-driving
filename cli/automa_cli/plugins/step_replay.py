"""Replay an image source through a prefix of the cycle's steps, offline.

``step_inspect`` replays every step through this path: the source's recorded
selections, restaged per frame, unless overridden;
then a report of the inspected step after every frame, optionally recorded as a
run that replays as a source again.
"""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from autonomy.plugins import uploaded_source
from autonomy.runtime.recording import recorded_selections, recording_frame_entry
from autonomy.decision_cycle.activation import StepActivation

from ..hosts.inspection_runs import (
    SharedMemorySeed, recorded_runner,
    default_selection, resolve_replay_activations, restage_replay_steps, selection_report,
)
from ..shared.paths import safe_path_part
from ..workbench.frames import FrameOutcome, run_frame
from ..workbench.source import (
    ImageSource,
    ReplayFrame,
    SourceValidationError,
    normalize_image_directory,
    normalize_image_file,
)


def read_replay_source(source: str | Path, *, max_frames: int) -> ImageSource:
    """Normalize an image or directory into replay frames and their selections.

    Raises SourceValidationError naming what is wrong with the source.
    """

    path = Path(source).expanduser()
    if max_frames <= 0:
        raise SourceValidationError("max_frames must be greater than zero")
    if path.is_file():
        return normalize_image_file(path)
    return normalize_image_directory(path, max_frames=max_frames)


class StepReplay:
    """Step runners that replay a source with the selections it recorded.

    Each step uses its override, else the selection the source's first frame
    recorded, else ``default_selection``. A frame that recorded a restage replaces
    that step's selection from then on, unless the step is overridden; the replaced runner
    is reset over the map. Starting from the first frame's selection, its
    restage keeps the runners and the map. Constructing the runners raises
    before any frame runs, including for a recorded release that is not
    available locally. A seed starts the run from its decoded map.
    """

    def __init__(
        self,
        image_source: ImageSource,
        *,
        steps: tuple[str, ...],
        overrides: dict[str, StepActivation] | None = None,
        seed: SharedMemorySeed | None = None,
    ) -> None:
        self.overrides = dict(overrides or {})
        first = recorded_selections(image_source.frames[0].metadata)
        self.activations = resolve_replay_activations(
            first, defaults={step: default_selection(step) for step in steps},
            overrides=self.overrides,
        )
        self.runners = {
            step: recorded_runner(activation) if activation is not None else None
            for step, activation in self.activations.items()
        }
        self.shared_memory = seed.shared_memory if seed is not None else {}

    def restage(self, frame: ReplayFrame) -> None:
        """Apply this frame's recorded selections, unless the step is overridden."""

        restage_replay_steps(
            self.runners, self.activations, recorded_selections(frame.metadata),
            overrides=set(self.overrides), shared_memory=self.shared_memory,
        )

    def run(self, frame: ReplayFrame) -> FrameOutcome:
        self.restage(frame)
        return run_frame(frame, steps=self.runners, shared_memory=self.shared_memory)

    def recording_entry(self, frame: ReplayFrame, outcome: FrameOutcome) -> dict[str, Any]:
        """Executed inputs, including overrides, independent of the report's frame filter."""
        applications = frame.control_applications
        return recording_frame_entry(
            frame_id=frame.frame_id, frame_index=frame.frame_index,
            timestamp_ms=frame.timestamp_ms,
            image_path=str(frame.image_path) if frame.image_path is not None else None,
            absence_reason=frame.absence_reason, steps=self.step_payloads(),
            context=outcome.result.context.to_dict(), mode=frame.mode,
            user_steering=frame.user_steering, user_throttle=frame.user_throttle,
            control_application_change=(
                {"kept": 0, "added": [item.to_dict() for item in applications]}
                if applications is not None else None
            ),
        )

    def step_payloads(self) -> dict[str, Any]:
        """Each step's current selection, as a frame records it for replay."""

        return {
            step: activation.to_payload() if activation is not None else None
            for step, activation in self.activations.items()
        }

    def selection_records(self) -> dict[str, Any]:
        """Each step's current selection, as a report records it for replay."""

        return {
            step: selection_report(activation) if activation is not None else None
            for step, activation in self.activations.items()
        }


def inspect_run_id(source_id: str) -> str:
    stamp = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    return f"inspect-{safe_path_part(source_id)}-{stamp}"


def record_inspect_run(
    run_dir: Path, report: dict[str, Any], image_source: ImageSource,
    replay_inputs: list[dict[str, Any]],
) -> None:
    """Archive executed inputs and images; the report may select only one frame.

    Raises OSError when the directory exists or cannot be written.
    """

    run_dir.mkdir(parents=True, exist_ok=False)
    report["run_dir"] = str(run_dir)
    (run_dir / "frames").mkdir()
    sources = {frame.frame_id: frame for frame in image_source.frames}
    entries = copy.deepcopy(replay_inputs)
    paths = {}
    for entry in entries:
        frame = sources[entry["frame_id"]]
        if frame.image_path is not None:
            relative = Path("frames") / f"frame_{frame.position:06d}{frame.image_path.suffix.lower()}"
            shutil.copyfile(frame.image_path, run_dir / relative)
            entry["image_path"] = relative.as_posix()
            paths[frame.frame_id] = relative.as_posix()
        _retain_uploaded_sources(run_dir, entry["steps"])
    selections = {entry["frame_id"]: entry["steps"] for entry in entries}
    for item in report["frames"]:
        item["image_path"] = paths.get(item["frame_id"])
        item["steps"] = selections[item["frame_id"]]
    (run_dir / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (run_dir / "manifest.json").write_text(
        json.dumps({"source_id": image_source.source_id, "frames": entries}, indent=2, sort_keys=True)
        + "\n", encoding="utf-8",
    )


def _retain_uploaded_sources(run_dir: Path, steps: dict[str, Any]) -> None:
    """Keep file-based plugin entrypoints relocatable, as runtime recordings do."""
    for activation in steps.values():
        if activation is None:
            continue
        metadata = activation.get("metadata") or {}
        sources = metadata.pop("plugin_sources", {})
        for plugin_id, spec in activation["plugin_specs"].items():
            module, separator, name = spec.partition(":")
            if not module.endswith(".py"):
                continue
            original = sources.get(module)
            source = Path(original or module)
            if original is None and not source.is_absolute():
                source = uploaded_source(module)
            data = source.read_bytes()
            filename = f"{hashlib.sha256(data).hexdigest()}.py"
            target = run_dir / "plugins" / filename
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(data)
            activation["plugin_specs"][plugin_id] = f"{filename}{separator}{name}"
