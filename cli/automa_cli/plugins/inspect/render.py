"""Text and JSON for an inspection report. No replay and no recording."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ...shared.memory_report import evidence_publisher, plugin_summaries
from ...shared.paths import display_path
from ..step_formatting import format_step_record, format_step_timing


def render_inspect(report: dict[str, Any], *, json_output: bool) -> str:
    if json_output:
        return json.dumps(report, indent=2, sort_keys=True)
    return _format_inspect_report(report)


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
