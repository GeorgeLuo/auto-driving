"""Step records and cycle timing as text, shared by inspect and live streams."""

from __future__ import annotations

import json
from typing import Any

from autonomy.decision_cycle.action.hold import HoldAction, idle_output
from autonomy.decision_cycle.confidence import confidence_text
from autonomy.decision_cycle.memory.interface import evidence_records
from autonomy.decision_cycle.timing import step_timing
from autonomy.decision_cycle.proposal.result import ProposalResult
from autonomy.decision_cycle.proposal.values import ActionProposal, ProposedVehicleCommand
from autonomy.decision_cycle.plan.values import ActionPlan
from autonomy.fields import FieldError, require_nested

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
    step: str, record: dict[str, object] | ProposalResult | ActionPlan | None, *,
    observation: dict[str, object] | None = None, memory: dict[str, object] | None = None,
) -> list[str]:
    if record is None:
        return [f"{step.capitalize()}: none (an earlier step failed or {step} is disabled)"]
    if step == "proposal":
        return _proposal_lines(record, observation=observation, memory=memory)
    if step == "plan":
        return _plan_lines(record)
    return _action_lines(record)


def _proposal_lines(
    record: dict[str, object] | ProposalResult, *, observation: dict[str, object] | None, memory: dict[str, object] | None,
) -> list[str]:
    if isinstance(record, ProposalResult):
        status, reason, candidates = record.status, record.reason, record.candidates
    else:
        status, reason = record.get("status"), record.get("reason")
        candidates = _read_candidates(record.get("candidates"))
    lines = [
        f"Proposal: status={status} reason={reason or '-'}",
        f"Observation: {observation['observation_id'] if observation is not None else 'none'}",
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
    lines.extend(_candidate_lines(candidates, selected=None))
    return lines


def _plan_lines(record: dict[str, object] | ActionPlan) -> list[str]:
    if isinstance(record, ActionPlan):
        selected, command, candidates = record.selected_proposal_id, record.command, record.candidates
        status, selector, reason, confidence = record.status, record.selector_id, record.reason, record.confidence
        contributions = [item.to_dict() for item in record.contributions]
    else:
        selected = record.get("selected_proposal_id")
        command_data = record.get("command")
        command = None if command_data is None else require_nested(
            ProposedVehicleCommand.from_dict, command_data, field_name="command",
        )
        candidates = _read_candidates(record.get("candidates"))
        status, selector, reason, confidence = (
            record.get("status"), record.get("selector_id"), record.get("reason"), record.get("confidence"),
        )
        contributions = record.get("contributions") if isinstance(record.get("contributions"), list) else []
    return [
        f"Plan: status={status} selector={selector} selected={selected}",
        f"Command: {json.dumps(_command(command), sort_keys=True)} "
        f"reason={reason} confidence={confidence_text(confidence)}",
        *_candidate_lines(candidates, selected=selected),
        "Contributions: " + (", ".join(
            f"{item.get('plugin_id')}(weight={item.get('weight')}, role={item.get('role')})"
            for item in contributions if isinstance(item, dict)
        ) or "none"),
    ]


def _action_lines(record: dict[str, Any]) -> list[str]:
    authority = record.get("authority") if isinstance(record.get("authority"), dict) else {}
    return [
        f"Action: status={record.get('status')} reason={record.get('reason') or '-'} "
        f"gate={authority.get('gate_id') or HoldAction.plugin_id}",
        f"Proposed: {json.dumps(authority.get('proposed'), sort_keys=True)}",
        f"Authorized: {json.dumps(authority.get('authorized_output') or idle_output(), sort_keys=True)}",
        # The action step passed the proposal through; the host decides whether it reaches the vehicle.
        f"Authorized as proposed: {str(authority.get('proposed_applied') is True).lower()}",
    ]


def _read_candidates(payload: object) -> tuple[ActionProposal, ...]:
    if payload is None:
        return ()
    if not isinstance(payload, list):
        raise FieldError("candidates", "must be an array")
    return tuple(
        require_nested(ActionProposal.from_dict, item, field_name=f"candidates.{index}")
        for index, item in enumerate(payload)
    )


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
