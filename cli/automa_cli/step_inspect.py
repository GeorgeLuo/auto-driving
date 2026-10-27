"""Inspect any plugin step by replaying the whole cycle offline."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from autonomy.decision_cycle.activation import DECISION_STEPS, STEPS
from autonomy.shared_memory import SharedMemoryValueError
from implementations.decision_cycle.catalog import selection_activation

from .decision import CommandResult
from .decision_steps import format_step_record, format_step_timing
from .inspection_runs import add_seed_provenance, read_shared_memory_seed
from .memory_report import evidence_publisher, plugin_summaries
from .paths import ROOT, display_path
from .step_replay import StepReplay, inspect_run_id, read_replay_source, record_inspect_run
from .workbench_source import WORKBENCH_DEFAULT_MAX_FRAMES, SourceValidationError

INSPECT_STEPS = ("perception", "memory", *DECISION_STEPS)


def _serialized_record(value: Any) -> dict[str, Any] | None:
    return value.to_dict() if value is not None else None


_RECORDS = {
    "perception": lambda result: _serialized_record(result.perception),
    "observation": lambda result: _serialized_record(result.observation),
    "memory": lambda result: result.memory,
    "proposal": lambda result: _serialized_record(result.proposal),
    "plan": lambda result: _serialized_record(result.plan),
    "action": lambda result: _serialized_record(result.action),
}
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
    frame: int | None = None,
    record: bool = False,
    json_output: bool = False,
    max_frames: int = WORKBENCH_DEFAULT_MAX_FRAMES,
    shared_memory_file: Path | None = None,
) -> CommandResult:
    """Replay a recording or images through the cycle and report ``step`` per frame.

    Every step runs the selection the source recorded, restaged per frame, or
    its default; ``plugins`` replaces the inspected step's. ``frame`` reports
    only that 0-based frame, after replaying the frames before it.
    """

    try:
        seed = read_shared_memory_seed(shared_memory_file)
    except (OSError, SharedMemoryValueError) as exc:
        return CommandResult(2, f"Could not read shared memory file: {exc}")
    try:
        image_source, manifest = read_replay_source(source, max_frames=max_frames)
    except SourceValidationError as exc:
        return CommandResult(2, f"Could not read {step} inspect source: {exc}")
    count = len(image_source.frames)
    if frame is not None and not 0 <= frame < count:
        return CommandResult(2, f"--frame {frame} is outside the source's {count} frames (0-{count - 1}).")
    try:
        overrides = None
        if preset is not None or plugins is not None:
            overrides = {step: selection_activation(step, preset=preset, plugins=plugins)}
        replay = StepReplay(image_source, manifest, steps=STEPS, overrides=overrides, seed=seed)
    except Exception as exc:  # Plugin construction is a CLI preflight boundary.
        return CommandResult(2, f"Could not load plugins for {step} inspect: {type(exc).__name__}: {exc}")

    frames: list[dict[str, Any]] = []
    for replay_frame in image_source.frames[: count if frame is None else frame + 1]:
        try:
            outcome = replay.run(replay_frame)
        except Exception as exc:  # Plugins are third-party code; name the frame that broke.
            return CommandResult(
                2, f"{step.capitalize()} inspect failed at {replay_frame.frame_id}: {type(exc).__name__}: {exc}"
            )
        if frame is not None and replay_frame.position != frame:
            continue
        step_record = None if replay_frame.absent else _RECORDS[step](outcome.result)
        frames.append({
            "position": replay_frame.position,
            "frame_id": replay_frame.frame_id,
            "frame_index": replay_frame.frame_index,
            "timestamp_ms": replay_frame.timestamp_ms,
            "image_path": str(replay_frame.image_path) if replay_frame.image_path is not None else None,
            "absence_reason": replay_frame.absence_reason,
            "record": step_record,
            "plugin_report": outcome.plugin_reports.get(step),
            "timing": outcome.result.timing,
            "steps": replay.step_payloads(),
            **{name: _RECORDS[name](outcome.result) for name in _INPUTS.get(step, ())},
        })

    run_id = inspect_run_id(image_source.source_id)
    report = {
        "schema": f"{step}_inspect_v1",
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
    }
    add_seed_provenance(report, seed)
    if record:
        run_dir = inspect_root(step) / run_id
        try:
            record_inspect_run(run_dir, report, image_source)
        except OSError as exc:
            return CommandResult(2, f"Could not record {step} inspect run {run_dir}: {exc}")
    if json_output:
        return CommandResult(0, json.dumps(report, indent=2, sort_keys=True))
    return CommandResult(0, _format_inspect_report(report))


def _format_inspect_report(report: dict[str, Any]) -> str:
    step = report["step"]
    source = report["source"]
    lines = [
        f"{step.capitalize()} inspect",
        "-" * len(f"{step} inspect"),
        f"Source: {source['path']} ({source['frame_count']} frames)",
        "Steps: " + ", ".join(
            f"{name}={','.join(selection['plugins']) or '-'}" if selection else f"{name}=off"
            for name, selection in report["selections"].items()
        ),
    ]
    for item in report["frames"]:
        lines.extend(["", f"Frame {item['position']}: {item['frame_id']}"])
        if item["absence_reason"]:
            lines.append(f"Absent: {item['absence_reason']}")
        lines.extend(_record_lines(
            step, item["record"], observation=item.get("observation"), memory=item.get("memory"),
        ))
        lines.append(format_step_timing(step, item["timing"]))
    if report["run_dir"]:
        lines.extend(["", f"Recorded: {display_path(Path(report['run_dir']))}"])
    return "\n".join(lines)


def _record_lines(
    step: str, record: dict[str, Any] | None, *,
    observation: dict[str, Any] | None = None, memory: dict[str, Any] | None = None,
) -> list[str]:
    if record is None:
        return [f"{step.capitalize()}: none"]
    if step == "perception":
        return [
            f"Perception: status={record.get('status')} "
            f"signals={len(record.get('signals') or [])} things={len(record.get('things') or [])}",
            *(
                f"  {run['plugin_id']}: status={run['status']} "
                f"signals={run.get('signal_count', 0)} things={run.get('thing_count', 0)}"
                + (f" error={run['error']}" if run.get('error') else "")
                for run in record.get("plugin_runs") or []
            ),
        ]
    if step == "memory":
        return [
            *(
                f"Memory: plugin={item['plugin_id']} health={item['health']} "
                f"records={item['record_count']} epoch={item['epoch_id']}"
                for item in plugin_summaries(record)
            ),
            f"Memory: publisher={evidence_publisher(record) or 'none'}",
            (f"Observation: {observation['observation_id']} "
             f"things={len(observation.get('things') or [])} signals={len(observation.get('signals') or [])}"
             if observation is not None else "Observation: none"),
        ]
    return format_step_record(step, record, observation=observation, memory=memory)
