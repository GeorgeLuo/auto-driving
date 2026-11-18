"""Replay a source through the cycle and build one step's inspection report."""

from __future__ import annotations

import os
from pathlib import Path

from autonomy.decision_cycle.activation import DECISION_STEPS, STEPS
from autonomy.plugins import LocalPluginCatalog
from autonomy.shared_memory import SharedMemoryValueError
from implementations.decision_cycle.catalog import selection_activation

from ...replay.manifest import apply_collection_manifest, check_manifest_selection
from ...replay.models import ReplayFrame, SourceValidationError
from ...replay.selections import add_seed_provenance, read_shared_memory_seed
from ...replay.source import WORKBENCH_DEFAULT_MAX_FRAMES
from ...shared.paths import ROOT
from .models import InspectResult
from .replay import StepReplay, inspect_run_id, read_replay_source, record_inspect_run

INSPECT_STEPS = ("perception", "memory", *DECISION_STEPS)

_INPUTS = {"memory": ("observation",), "proposal": ("observation", "memory")}


def inspect_root(step: str) -> Path:
    return Path(os.environ.get(
        f"AUTOMA_{step.upper()}_INSPECT_ROOT", ROOT / "runtime" / f"{step}-inspections",
    ))


def inspect_step(
    step: str,
    *,
    source: str | Path,
    plugins: list[str] | None = None,
    preset: str | None = None,
    manifest: Path | None = None,
    frame: int | None = None,
    record: bool = False,
    max_frames: int = WORKBENCH_DEFAULT_MAX_FRAMES,
    shared_memory_file: Path | None = None,
) -> InspectResult:
    """Replay a recording or images through the cycle and report ``step`` per frame.

    Every step runs the selection the source recorded, restaged per frame, or
    its default; ``plugins`` replaces the inspected step's. ``frame`` reports
    only that 0-based frame, after replaying the frames before it.
    """

    try:
        check_manifest_selection(manifest, preset, plugins)
    except ValueError as exc:
        return InspectResult(2, error=str(exc))
    try:
        seed = read_shared_memory_seed(shared_memory_file)
    except (OSError, SharedMemoryValueError) as exc:
        return InspectResult(2, error=f"Could not read shared memory file: {exc}")
    try:
        image_source = read_replay_source(source, max_frames=max_frames)
    except SourceValidationError as exc:
        return InspectResult(2, error=f"Could not read {step} inspect source: {exc}")
    count = len(image_source.frames)
    if frame is not None and not 0 <= frame < count:
        return InspectResult(2, error=f"--frame {frame} is outside the source's {count} frames (0-{count - 1}).")
    try:
        provenance: dict[str, object] = {}
        if manifest is not None:
            # Keep the catalog and its uploaded sources alive through inspect --record.
            catalog = LocalPluginCatalog()
            image_source, manifest_provenance = apply_collection_manifest(image_source, manifest, catalog)
            provenance["collection_manifest"] = manifest_provenance
        overrides = None
        if preset is not None or plugins is not None:
            overrides = {step: selection_activation(step, preset=preset, plugins=plugins)}
        replay = StepReplay(image_source, steps=STEPS, overrides=overrides, seed=seed)
    except Exception as exc:  # Plugin construction is a CLI preflight boundary.
        return InspectResult(2, error=f"Could not load plugins for {step} inspect: {type(exc).__name__}: {exc}")

    try:
        frames, records, replay_inputs = _inspect_frames(step, replay, image_source.frames, frame=frame, record=record)
    except RuntimeError as exc:
        return InspectResult(2, error=str(exc))

    run_id = inspect_run_id(image_source.source_id)
    report = {
        "schema": f"{step}_inspect",
        "step": step,
        "run_id": run_id,
        "source_id": image_source.source_id,
        "source": {
            "path": str(image_source.source_path),
            "source_id": image_source.source_id,
            "frame_count": count,
        },
        "selections": replay.selection_records(),
        "frames": frames,
        "run_dir": None,
        **provenance,
    }
    add_seed_provenance(report, seed)
    if record:
        run_dir = inspect_root(step) / run_id
        try:
            record_inspect_run(run_dir, report, image_source, replay_inputs)
        except (OSError, ValueError) as exc:
            return InspectResult(2, error=f"Could not record {step} inspect run {run_dir}: {exc}")
    return InspectResult(0, report=report, records=tuple(records))


def _inspect_frames(
    step: str, replay: StepReplay, source_frames: tuple[ReplayFrame, ...], *, frame: int | None, record: bool,
):
    """Execute the prefix needed for inspection and report the requested frames."""

    frames: list[dict[str, object]] = []
    records = []
    replay_inputs: list[dict[str, object]] = []
    for replay_frame in source_frames[: len(source_frames) if frame is None else frame + 1]:
        try:
            outcome = replay.run(replay_frame)
        except Exception as exc:  # Plugins are third-party code; name the frame that broke.
            raise RuntimeError(
                f"{step.capitalize()} inspect failed at {replay_frame.frame_id}: {type(exc).__name__}: {exc}"
            ) from exc
        if record:
            replay_inputs.append(replay.recording_entry(replay_frame, outcome))
        if frame is not None and replay_frame.position != frame:
            continue
        result = outcome.result
        records.append(None if replay_frame.absent else result)
        step_record = None if replay_frame.absent else getattr(result, step)
        frames.append({
            "position": replay_frame.position,
            "frame_id": replay_frame.frame_id,
            "frame_index": replay_frame.frame_index,
            "timestamp_ms": replay_frame.timestamp_ms,
            "image_path": str(replay_frame.image_path) if replay_frame.image_path is not None else None,
            "absence_reason": replay_frame.absence_reason,
            "record": None if step_record is None else step_record.to_dict(),
            "plugin_report": outcome.plugin_reports.get(step),
            "timing": result.timing,
            "steps": replay.step_payloads(),
            **{name: None if getattr(result, name) is None else getattr(result, name).to_dict()
               for name in _INPUTS.get(step, ())},
        })
    return frames, records, replay_inputs
