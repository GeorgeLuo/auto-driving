"""Step records and cycle timing as text, shared by inspect and live streams."""

from __future__ import annotations

import json
from typing import Any

from autonomy.decision_cycle.action.hold import HoldAction, idle_output
from autonomy.decision_cycle.confidence import confidence_text
from autonomy.decision_cycle.timing import step_timing

from ..shared.memory_report import evidence_publisher, evidence_records, plugin_summaries

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
    step: str, record: dict[str, Any] | None, *,
    observation: dict[str, Any] | None = None, memory: dict[str, Any] | None = None,
) -> list[str]:
    if record is None:
        return [f"{step.capitalize()}: none (an earlier step failed or {step} is disabled)"]
    if step == "proposal":
        return _proposal_lines(record, observation=observation, memory=memory)
    if step == "plan":
        return _plan_lines(record)
    return _action_lines(record)


def _proposal_lines(
    record: dict[str, Any], *, observation: dict[str, Any] | None, memory: dict[str, Any] | None,
) -> list[str]:
    lines = [
        f"Proposal: status={record.get('status')} reason={record.get('reason') or '-'}",
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
    lines.extend(_candidate_lines(record.get("candidates"), selected=None))
    return lines


def _plan_lines(record: dict[str, Any]) -> list[str]:
    selected = record.get("selected_proposal_id")
    contributions = record.get("contributions") if isinstance(record.get("contributions"), list) else []
    return [
        f"Plan: status={record.get('status')} selector={record.get('selector_id')} selected={selected}",
        f"Command: {json.dumps(_command(record.get('command')), sort_keys=True)} "
        f"reason={record.get('reason')} confidence={confidence_text(record.get('confidence'))}",
        *_candidate_lines(record.get("candidates"), selected=selected),
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


def _candidate_lines(candidates: Any, *, selected: str | None) -> list[str]:
    candidates = [item for item in (candidates if isinstance(candidates, list) else []) if isinstance(item, dict)]
    if not candidates:
        return ["Candidates: none"]
    return [
        ("* " if selected is not None and item.get("proposal_id") == selected else "- ")
        + f"plugin={item.get('plugin_id')} proposal={item.get('proposal_id')} "
        f"lifecycle={item.get('lifecycle')} freshness={item.get('freshness')} "
        f"confidence={confidence_text(item.get('confidence'))} reason={item.get('reason')} "
        f"command={json.dumps(_command(item.get('command')), sort_keys=True)}"
        for item in candidates
    ]


def _command(command: Any) -> dict[str, Any] | None:
    if not isinstance(command, dict):
        return None
    return {"steering": command.get("steering"), "throttle": command.get("throttle")}
