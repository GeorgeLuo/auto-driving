"""Inspect images through memory, and reset live memory.

``perception_runs`` holds perception inspect and the sensor-frame run.
Memory inspect is that inspect. Memory has no separate run entry. Reset is
the other ``vehicles memory`` command, and perception has no reset command,
so reset lives here.
"""

from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autonomy.decision_cycle.steps import builtin_activation, step_runner
from autonomy.decision_cycle.memory.interface import EPOCH_ID, HEALTH, RECORD_COUNT
from autonomy.decision_cycle.memory.runner import MemoryRunner
from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.perception.runner import PerceptionRunner
from autonomy.runtime.client import RuntimeClient
from implementations.decision_cycle.catalog import selection_activation

from .inspection_runs import recorded_selection, recorded_selections, replay_step, selection_record
from .memory import _selected_memory
from .memory_report import evidence_publisher, ledger_is_empty, plugin_summaries
from .paths import ROOT, display_path, safe_path_part
from .runtime_hosts import runtime_base_url
from .streaming import _live_plugins, probe_live_memory
from .vehicles import discover_active_vehicles, find_vehicle_by_id, format_active_vehicles
from .workbench_frames import run_frame
from .workbench_source import (
    WORKBENCH_DEFAULT_MAX_FRAMES,
    SourceValidationError,
    normalize_image_directory,
    normalize_image_file,
    read_image_manifest,
)

INSPECT_ROOT = Path(
    os.environ.get("AUTOMA_MEMORY_INSPECT_ROOT", ROOT / "runtime" / "memory-inspections")
)
MEMORY_INSPECT_SCHEMA = "memory_inspect_v1"
MEMORY_RESET_SCHEMA = "vehicle_memory_reset_v1"


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    message: str


def inspect_memory(
    source: str,
    *,
    preset: str | None = None,
    plugins: list[str] | None = None,
    record: bool = False,
    json_output: bool = False,
    max_frames: int = WORKBENCH_DEFAULT_MAX_FRAMES,
) -> CommandResult:
    """Run an image source through perception, observation and memory, frame by frame.

    Reports what each memory plugin retained after every frame. A recorded run
    restores its executable perception and memory selections; explicit flags
    override memory. Otherwise each step uses its default. It reads the source
    only; live frames come from ``perception inspect --record``.
    """

    activation, error = _selected_memory(preset, plugins)
    if error is not None:
        return error
    path = Path(source).expanduser()
    try:
        if max_frames <= 0:
            raise SourceValidationError("max_frames must be greater than zero")
        image_source = (
            normalize_image_file(path)
            if path.is_file()
            else normalize_image_directory(path, max_frames=max_frames)
        )
        _manifest_path, source_manifest = (
            read_image_manifest(image_source.source_path)
            if path.is_dir()
            else (None, None)
        )
    except SourceValidationError as exc:
        return CommandResult(2, f"Could not read memory inspect source: {exc}")
    try:
        source_manifest = source_manifest or {}
        perception_activation = (
            recorded_selection("perception", source_manifest) or selection_activation("perception")
        )
        if preset is None and plugins is None:
            activation = recorded_selection("memory", source_manifest) or activation
        perception_runner = PerceptionRunner.from_activation(perception_activation)
        memory_step = MemoryRunner.from_activation(activation)
        observation_activation = builtin_activation("observation")
        observation_step = step_runner(observation_activation)
    except Exception as exc:  # Plugin construction is a CLI preflight boundary.
        return CommandResult(2, f"Could not load plugins for memory inspect: {type(exc).__name__}: {exc}")

    shared_memory: dict[str, Any] = {}
    frames: list[dict[str, Any]] = []
    for frame in image_source.frames:
        try:
            selections = recorded_selections(frame.metadata)
            if "perception" in selections:
                perception_activation = selections["perception"] or selection_activation("perception", plugins=[])
                perception_runner = replay_step(perception_runner, perception_activation, shared_memory)
            if "memory" in selections and preset is None and plugins is None:
                activation = selections["memory"] or selection_activation("memory", plugins=[])
                memory_step = replay_step(memory_step, activation, shared_memory)
            if "observation" in selections:
                observation_activation = selections["observation"]
                observation_step = replay_step(observation_step, observation_activation, shared_memory)
            outcome = run_frame(
                frame,
                steps={
                    "perception": perception_runner, "memory": memory_step,
                    "observation": observation_step,
                },
                shared_memory=shared_memory,
            )
        except Exception as exc:  # Plugins are third-party code; name the frame that broke.
            return CommandResult(
                2, f"Memory inspect failed at {frame.frame_id}: {type(exc).__name__}: {exc}"
            )
        result = outcome.result
        frames.append(
            {
                "frame_id": frame.frame_id,
                "frame_index": frame.frame_index,
                "timestamp_ms": frame.timestamp_ms,
                "image_path": str(frame.image_path) if frame.image_path is not None else None,
                "absence_reason": frame.absence_reason,
                "plugins": plugin_summaries(result.memory),
                "evidence_publisher": evidence_publisher(result.memory),
                "observation": _observation_counts(result.observation),
                "steps": {
                    "perception": perception_activation.to_payload(),
                    "observation": observation_activation.to_payload() if observation_activation else None,
                    "memory": activation.to_payload(),
                },
            }
        )

    run_id = _inspect_run_id(image_source.source_id)
    report = {
        "schema": MEMORY_INSPECT_SCHEMA,
        "run_id": run_id,
        "source_id": image_source.source_id,
        "source": {
            "path": str(image_source.source_path),
            "source_id": image_source.source_id,
            "frame_count": len(frames),
        },
        "perception": {
            **selection_record(perception_activation),
            "plugins": list(perception_activation.plugins),
        },
        "memory": {**selection_record(activation), "plugins": list(activation.plugins)},
        "frames": frames,
        "final": memory_step.report(),
        "run_dir": None,
    }
    if record:
        run_dir = INSPECT_ROOT / run_id
        try:
            run_dir.mkdir(parents=True, exist_ok=False)
            report["run_dir"] = str(run_dir)
            frames_dir = run_dir / "frames"
            frames_dir.mkdir()
            for frame, recorded_frame in zip(image_source.frames, report["frames"]):
                if frame.image_path is not None:
                    relative = (
                        Path("frames") / f"frame_{frame.position:06d}{frame.image_path.suffix.lower()}"
                    )
                    shutil.copyfile(frame.image_path, run_dir / relative)
                    recorded_frame["image_path"] = relative.as_posix()
            (run_dir / "report.json").write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
        except OSError as exc:
            return CommandResult(2, f"Could not record memory inspect run {run_dir}: {exc}")
    if json_output:
        return CommandResult(0, json.dumps(report, indent=2, sort_keys=True))
    return CommandResult(0, _format_inspect_report(report))


def _observation_counts(observation: Observation | None) -> dict[str, Any] | None:
    if observation is None:
        return None
    return {
        "observation_id": observation.observation_id,
        "things": len(observation.things),
        "signals": len(observation.signals),
    }


def _inspect_run_id(source_id: str) -> str:
    stamp = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    return f"inspect-{safe_path_part(source_id)}-{stamp}"


def _format_inspect_report(report: dict[str, Any]) -> str:
    source = report["source"]
    lines = [
        "Memory inspect",
        "--------------",
        f"Source: {source['path']} ({source['frame_count']} frames)",
        f"Perception: {report['perception']['preset']} ({', '.join(report['perception']['plugins'])})",
        f"Memory: {report['memory']['preset']} ({', '.join(report['memory']['plugins'])})",
        "",
        "Frame  Plugin  Health  Records  Epoch  Publisher  Observation",
    ]
    for frame in report["frames"]:
        observation = frame["observation"]
        seen = f"{observation['things']}t/{observation['signals']}s" if observation else "-"
        publisher = frame["evidence_publisher"] or "-"
        for item in frame["plugins"]:
            lines.append(
                f"{frame['frame_index']}  {item['plugin_id']}  {item['health']}  "
                f"{item['record_count']}  {item['epoch_id']}  {publisher}  {seen}"
            )
    lines.append("")
    lines.append("Final")
    for item in plugin_summaries(report["final"]):
        lines.append(
            f"  {item['plugin_id']}: {item['health']}, "
            f"{item['record_count']} records, epoch {item['epoch_id']}"
        )
    lines.append(f"  Evidence publisher: {evidence_publisher(report['final']) or 'none'}")
    if report["run_dir"]:
        lines.extend(["", f"Recorded: {display_path(Path(report['run_dir']))}"])
    return "\n".join(lines)


def reset_vehicle_memory(
    *,
    vehicle_id: str,
    timeout_s: float = 3.0,
    wait_s: float = 5.0,
    json_output: bool = False,
) -> CommandResult:
    """Reset live memory on the vehicle's runtime host.

    The reset is confirmed when the live probe afterwards shows every applied
    plugin's ledger empty (``ledger_is_empty``); ``nonempty_plugin_ids`` names
    any that still hold records. Operators can check with ``info memory``,
    ``stream memory``, or the Memory map.
    """

    discovery = discover_active_vehicles(
        timeout_s=timeout_s,
        include_picar=True,
        include_chase_sim=True,
        include_inactive=True,
    )
    vehicle, error = find_vehicle_by_id(discovery, vehicle_id)
    if error:
        return CommandResult(
            2,
            "\n\n".join(
                [
                    error,
                    "Discovery:",
                    format_active_vehicles(discovery, include_inactive=True),
                ]
            ),
        )
    if vehicle is None:
        return CommandResult(2, f"Vehicle {vehicle_id!r} was not found.")

    provider = vehicle.get("provider")
    before = probe_live_memory(
        vehicle_id=vehicle_id,
        vehicle=vehicle,
        timeout_s=timeout_s,
    )
    if before.get("status") == "absent":
        return CommandResult(
            2,
            "\n".join(
                [
                    f"No live memory step to reset for {vehicle_id!r}.",
                    str(before.get("error") or "Memory component is absent."),
                ]
            ),
        )
    if before.get("status") not in {"live", "error"}:
        return CommandResult(
            2,
            "\n".join(
                [
                    f"Cannot reset memory for {vehicle_id!r}: live status is {before.get('status')!r}.",
                    str(before.get("error") or "Start automation (Chase) or deploy autonomy (Pi) first."),
                ]
            ),
        )

    try:
        base_url = runtime_base_url(vehicle_id)
        reset_payload = RuntimeClient(base_url, timeout_s=timeout_s).reset_memory()
    except RuntimeError as exc:
        return CommandResult(2, f"Memory reset failed for {vehicle_id}: {exc}")

    after = probe_live_memory(
        vehicle_id=vehicle_id,
        vehicle=vehicle,
        timeout_s=timeout_s,
    )
    payload = {
        "schema": MEMORY_RESET_SCHEMA,
        "vehicle_id": vehicle_id,
        "provider": provider,
        "ok": bool(reset_payload.get("ok")),
        "reset": reset_payload,
        "before": before,
        "after": after,
    }
    if not payload["ok"]:
        if json_output:
            return CommandResult(2, json.dumps(payload, indent=2, sort_keys=True))
        return CommandResult(
            2,
            "\n".join(
                [
                    f"Memory reset failed: {vehicle_id}",
                    str(reset_payload.get("error") or reset_payload.get("status") or "unknown error"),
                ]
            ),
        )

    # Operator-facing confirmation: every applied plugin's ledger is empty.
    nonempty = [entry["plugin_id"] for entry in _live_plugins(after) if not ledger_is_empty(entry)]
    confirmed = after.get("status") == "live" and not nonempty
    payload["confirmed_empty"] = confirmed
    payload["nonempty_plugin_ids"] = nonempty
    if json_output:
        return CommandResult(0 if confirmed else 2, json.dumps(payload, indent=2, sort_keys=True))
    before_plugins = {entry["plugin_id"]: entry for entry in _live_plugins(before)}
    after_plugins = {entry["plugin_id"]: entry for entry in _live_plugins(after)}
    lines = [
        f"Reset memory: {vehicle_id}",
        f"Plugins: {', '.join(after.get('plugin_ids') or before.get('plugin_ids') or []) or '—'}",
    ]
    for plugin_id in [*after_plugins, *(key for key in before_plugins if key not in after_plugins)]:
        was = before_plugins.get(plugin_id, {})
        now = after_plugins.get(plugin_id, {})
        lines.append(
            f"  {plugin_id}: "
            f"epoch {was.get(EPOCH_ID) or '—'} -> {now.get(EPOCH_ID) or '—'}, "
            f"records {was.get(RECORD_COUNT)} -> {now.get(RECORD_COUNT)}, "
            f"health {was.get(HEALTH)} -> {now.get(HEALTH)}"
        )
    lines.extend(
        [
            f"Evidence publisher: {before.get('evidence_publisher') or 'none'} -> "
            f"{after.get('evidence_publisher') or 'none'}",
            f"Resets: {before.get('reset_count')} -> {after.get('reset_count')}",
        ]
    )
    if not confirmed:
        detail = f" Still holding records: {', '.join(nonempty)}." if nonempty else ""
        lines.append(f"Warning: live probe did not confirm an empty memory after reset.{detail}")
        return CommandResult(2, "\n".join(lines))
    return CommandResult(0, "\n".join(lines))
