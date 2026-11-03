"""Pure frame presentation for the workbench's live state and replay history."""

from __future__ import annotations

import copy
from typing import Any

from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.perception.interface import PerceptionText

from ..shared.memory_report import evidence_publisher, plugin_states, plugin_summaries
from ..replay.frames import FrameOutcome
from ..replay.source import ReplayFrame


def empty_steps() -> dict[str, Any]:
    """The per-step payloads of the displayed frame, before any frame ran."""

    return {"perception": None, "observation": None, "memory": None, "decision": None}


def displayed_steps(outcome: FrameOutcome) -> dict[str, Any]:
    """The cycle's records grouped for the workbench, detached from runner outputs."""

    result = outcome.result
    return {
        "perception": result.perception.to_dict() if result.perception else None,
        "observation": result.observation.to_dict() if result.observation else None,
        "memory": copy.deepcopy(result.memory),
        "decision": copy.deepcopy(outcome.decision),
    }


def frame_summary(
    *,
    perception: PerceptionText | None = None,
    observation: Observation | None = None,
    memory: Any = None,
    duration_ms: int | float | None = None,
    frames_completed: int,
    frames_total: int,
) -> dict[str, Any]:
    return {
        "frames_completed": int(frames_completed),
        "frames_total": int(frames_total),
        "perception_status": perception.status if perception else None,
        "perception_things": len(perception.things) if perception else 0,
        "perception_signals": len(perception.signals) if perception else 0,
        "observation_available": observation is not None,
        "memory_plugins": plugin_summaries(memory),
        "memory_evidence_publisher": evidence_publisher(memory),
        "last_duration_ms": round(float(duration_ms), 3)
        if duration_ms is not None
        else None,
    }


def frame_detail(
    *,
    frame: ReplayFrame,
    outcome: FrameOutcome,
    steps: dict[str, Any],
    previous_memory: dict[str, Any] | None,
) -> dict[str, Any]:
    result = outcome.result
    memory = steps["memory"]
    return {
        "frame": frame.to_dict(include_path=False),
        "steps": copy.deepcopy(steps),
        "perception_status": result.perception.status
        if result.perception
        else None,
        "memory_plugins": plugin_summaries(memory),
        "memory_evidence_publisher": evidence_publisher(memory),
        "memory_effects": _memory_effects(previous_memory, memory),
        "duration_ms": result.duration_ms,
        "timing": copy.deepcopy(result.timing),
    }


def timeline_item(detail: dict[str, Any]) -> dict[str, Any]:
    frame = detail["frame"]
    return {
        "frame": {
            "frame_id": frame["frame_id"],
            "frame_index": frame["frame_index"],
            "position": frame["position"],
            "timestamp_ms": frame["timestamp_ms"],
            "absent": frame["absent"],
            "absence_reason": frame["absence_reason"],
        },
        "perception_status": detail["perception_status"],
        "memory_plugins": copy.deepcopy(detail["memory_plugins"]),
        "memory_evidence_publisher": detail["memory_evidence_publisher"],
        "memory_effects": copy.deepcopy(detail["memory_effects"]),
        "decision": _decision_timeline_item(detail["steps"]["decision"]),
        "duration_ms": detail["duration_ms"],
        "timing": copy.deepcopy(detail.get("timing")),
    }


def _decision_timeline_item(decision: Any) -> dict[str, Any]:
    cycle = decision if isinstance(decision, dict) else {}
    plan = cycle.get("plan") if isinstance(cycle.get("plan"), dict) else {}
    authority = (
        cycle.get("authority")
        if isinstance(cycle.get("authority"), dict)
        else {}
    )
    proposed = authority.get("proposed")
    return {
        "status": cycle.get("status"),
        "selected_proposal_id": plan.get("selected_proposal_id"),
        "proposed_steering": (
            proposed.get("steering") if isinstance(proposed, dict) else None
        ),
        "proposed_applied": authority.get("proposed_applied", False),
    }


def _record_ids(state: dict[str, Any] | None) -> set[str]:
    records = (state or {}).get("records")
    if not isinstance(records, (list, tuple)):
        return set()
    return {str(item.get("record_id")) for item in records if isinstance(item, dict)}


def _memory_effects(previous: Any, current: Any) -> list[dict[str, Any]]:
    """Each plugin's record ids added, removed and retained since the shown frame.

    Plugins are matched by ``plugin_id``. A plugin the previous report did not
    list starts from no records.
    """

    before = {plugin_id: _record_ids(state) for plugin_id, state in plugin_states(previous)}
    effects: list[dict[str, Any]] = []
    for plugin_id, state in plugin_states(current):
        now = _record_ids(state)
        prior = before.get(plugin_id, set())
        effects.append(
            {
                "plugin_id": plugin_id,
                "added": sorted(now - prior),
                "removed": sorted(prior - now),
                "retained": sorted(now & prior),
            }
        )
    return effects
