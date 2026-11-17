"""Text and JSON for an inspection report. No replay and no recording."""

from __future__ import annotations

import json
from pathlib import Path
from autonomy.decision_cycle.cycle import DecisionCycleResult
from autonomy.decision_cycle.action.result import ActionResult
from autonomy.decision_cycle.plan.values import ActionPlan
from autonomy.decision_cycle.proposal.result import ProposalResult
from autonomy.decision_cycle.perception.interface import PerceptionText
from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.memory.interface import MemoryReport

from ...shared.memory_report import evidence_publisher, plugin_summaries
from ...shared.paths import display_path
from ..step_formatting import format_step_record, format_step_timing


def render_inspect(
    report: dict[str, object], *, json_output: bool,
    records: tuple[DecisionCycleResult | None, ...],
) -> str:
    if json_output:
        return json.dumps(report, indent=2, sort_keys=True)
    return _format_inspect_report(report, records=records)


def _format_inspect_report(
    report: dict[str, object], *, records: tuple[DecisionCycleResult | None, ...],
) -> str:
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
    for index, item in enumerate(report["frames"]):
        lines.extend(["", f"Frame {item['position']}: {item['frame_id']}"])
        if item["absence_reason"]:
            lines.append(f"Absent: {item['absence_reason']}")
        result = records[index]
        lines.extend(_record_lines(
            step, None if result is None else getattr(result, step),
            observation=None if result is None else result.observation,
            memory=None if result is None else result.memory,
        ))
        lines.append(format_step_timing(step, item["timing"]))
    if report["run_dir"]:
        lines.extend(["", f"Recorded: {display_path(Path(report['run_dir']))}"])
    return "\n".join(lines)


def _record_lines(
    step: str, record: PerceptionText | MemoryReport | ProposalResult | ActionPlan | ActionResult | None, *,
    observation: Observation | None = None, memory: MemoryReport | None = None,
) -> list[str]:
    if record is None:
        return [f"{step.capitalize()}: none"]
    if isinstance(record, PerceptionText):
        return [
            f"Perception: status={record.status} "
            f"signals={len(record.signals)} things={len(record.things)}",
            *(
                f"  {run.plugin_id}: status={run.status} "
                f"signals={run.signal_count} things={run.thing_count}"
                + (f" error={run.error}" if run.error else "")
                for run in record.plugin_runs
            ),
        ]
    if isinstance(record, MemoryReport):
        return [
            *(
                f"Memory: plugin={item['plugin_id']} health={item['health']} "
                f"records={item['record_count']} epoch={item['epoch_id']}"
                for item in plugin_summaries(record)
            ),
            f"Memory: publisher={evidence_publisher(record) or 'none'}",
            (f"Observation: {observation.observation_id} "
             f"things={len(observation.things)} signals={len(observation.signals)}"
             if observation is not None else "Observation: none"),
        ]
    return format_step_record(step, record, observation=observation, memory=memory)
