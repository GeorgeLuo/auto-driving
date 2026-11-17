"""Step records and cycle timing as text, shared by inspect and live streams."""

from __future__ import annotations

import json
from typing import Any

from autonomy.decision_cycle.action.result import ActionResult
from autonomy.decision_cycle.confidence import confidence_text
from autonomy.decision_cycle.memory.interface import MemoryReport, evidence_records
from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.timing import step_timing
from autonomy.decision_cycle.proposal.result import ProposalResult
from autonomy.decision_cycle.proposal.values import ActionProposal, ProposedVehicleCommand
from autonomy.decision_cycle.plan.values import ActionPlan
from autonomy.serialization import frozen_mapping_to_dict

from ..shared.memory_report import evidence_publisher, plugin_summaries

_RETAINED_SHOWN = 12


def format_step_timing(step: str, timing: dict[str, Any] | None) -> str:
    """``step``'s and its plugins' time in one cycle's ``timing``, then the cycle's."""

    entry = step_timing(timing, step)
    if entry is None:
        return "Timing: none"
    plugins = ", ".join(
        f"{plugin.get('plugin_id')} {_ms(plugin.get('duration_ms'))}"
        for plugin in entry.get("plugins") or ()
        if isinstance(plugin, dict)
    )
    return (
        f"Timing: {step} {_ms(entry.get('duration_ms'))}"
        + (f" ({plugins})" if plugins else "")
        + f"; cycle {_ms(timing.get('duration_ms'))}"
    )


def _ms(value: Any) -> str:
    return f"{value:.3f} ms" if isinstance(value, (int, float)) else "-"


def format_step_record(
    step: str, record: ProposalResult | ActionPlan | ActionResult | None, *,
    observation: Observation | None = None, memory: MemoryReport | None = None,
) -> list[str]:
    if record is None:
        return [f"{step.capitalize()}: none (an earlier step failed or {step} is disabled)"]
    if isinstance(record, ProposalResult):
        return _proposal_lines(record, observation=observation, memory=memory)
    if isinstance(record, ActionPlan):
        return _plan_lines(record)
    return _action_lines(record)


def _proposal_lines(
    record: ProposalResult, *, observation: Observation | None, memory: MemoryReport | None,
) -> list[str]:
    lines = [
        f"Proposal: status={record.status} reason={record.reason or '-'}",
        f"Observation: {observation.observation_id if observation is not None else 'none'}",
    ]
    lines.extend(
        f"Memory: plugin={item['plugin_id']} health={item['health']} "
        f"records={item['record_count']} epoch={item['epoch_id']}"
        for item in plugin_summaries(memory)
    )
    lines.append(f"Memory: publisher={evidence_publisher(memory) or 'none'}")
    for item in (evidence_records(memory) or [])[:_RETAINED_SHOWN]:
        origin = item.get("origin") or {}
        lines.append(
            f"Retained: record_id={item.get('record_id')} kind={item.get('kind')} "
            f"confidence={confidence_text(item.get('confidence'))} frame_id={origin.get('frame_id')}"
        )
    lines.extend(_candidate_lines(record.candidates, selected=None))
    return lines


def _plan_lines(record: ActionPlan) -> list[str]:
    selected = record.selected_proposal_id
    return [
        f"Plan: status={record.status} selector={record.selector_id} selected={selected}",
        f"Command: {json.dumps(_command(record.command), sort_keys=True)} "
        f"reason={record.reason} confidence={confidence_text(record.confidence)}",
        *_candidate_lines(record.candidates, selected=selected),
        "Contributions: " + (", ".join(
            f"{item.plugin_id}(weight={item.weight}, role={item.role})"
            for item in record.contributions
        ) or "none"),
    ]


def _action_lines(record: ActionResult) -> list[str]:
    authority = record.authority
    return [
        f"Action: status={record.status} reason={record.reason or '-'} gate={authority.gate_id}",
        f"Proposed: {json.dumps(None if authority.proposed is None else authority.proposed.to_dict(), sort_keys=True)}",
        f"Authorized: {json.dumps(frozen_mapping_to_dict(authority.authorized_output), sort_keys=True)}",
        # The action step passed the proposal through; the host decides whether it reaches the vehicle.
        f"Authorized as proposed: {str(authority.proposed_applied).lower()}",
    ]


def _candidate_lines(candidates: tuple[ActionProposal, ...], *, selected: str | None) -> list[str]:
    if not candidates:
        return ["Candidates: none"]
    return [
        ("* " if selected is not None and item.proposal_id == selected else "- ")
        + f"plugin={item.plugin_id} proposal={item.proposal_id} "
        f"lifecycle={item.lifecycle} freshness={item.freshness} "
        f"confidence={confidence_text(item.confidence)} reason={item.reason} "
        f"command={json.dumps(_command(item.command), sort_keys=True)}"
        for item in candidates
    ]


def _command(command: ProposedVehicleCommand | None) -> dict[str, float] | None:
    if command is None:
        return None
    return {"steering": command.steering, "throttle": command.throttle}
