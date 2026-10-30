"""The plan record the plan step produces.

``ActionPlan`` records one cycle's candidates and the command the action step
applies, with its reason and optional confidence. Its ``status`` is one of:

- ``idle``: no command, reason, confidence, or contributions.
- ``selected``: one candidate's command, reason, and confidence, named by
  ``selected_proposal_id`` and one contribution of weight 1.0.
- ``composed``: the plan's own command, built from the candidates its
  contributions name, each with a finite weight and a role.

Its validation holds these rules for every plan, whichever code constructs
it. The number of candidates is the number of selected proposal plugins; each
candidate is bounded by its own proposal limits.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from autonomy.decision_cycle.confidence import optional_confidence
from autonomy.decision_cycle.proposal.values import ActionProposal, ProposedVehicleCommand
from autonomy.serialization import (
    deep_freeze,
    frozen_mapping_to_dict,
)
from autonomy.decision_cycle.action_identifiers import (
    plan_id_for,
    require_ascii_id,
    require_safe_int,
)

ACTION_PLAN_SCHEMA = "action_plan"


@dataclass(frozen=True)
class PlanContribution:
    proposal_id: str
    plugin_id: str
    weight: float = 1.0
    role: str = "selected"

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "plugin_id": self.plugin_id,
            "weight": self.weight,
            "role": self.role,
        }


@dataclass(frozen=True)
class ActionPlan:
    frame_id: str
    timestamp_ms: int
    status: str
    candidates: tuple[ActionProposal, ...]
    # The plugin ID of the plan plugin that built the plan.
    selector_id: str
    selected_proposal_id: str | None = None
    contributions: tuple[PlanContribution, ...] = ()
    command: ProposedVehicleCommand | None = None
    reason: str = ""
    confidence: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    plan_id: str = ""
    schema: str = ACTION_PLAN_SCHEMA

    def __post_init__(self) -> None:
        frame_id = require_ascii_id(self.frame_id, field_name="frame_id")
        object.__setattr__(self, "frame_id", frame_id)
        object.__setattr__(
            self,
            "timestamp_ms",
            require_safe_int(self.timestamp_ms, field_name="timestamp_ms"),
        )
        plan_id = self.plan_id or plan_id_for(frame_id)
        if plan_id != plan_id_for(frame_id):
            raise ValueError(f"plan_id must be {plan_id_for(frame_id)!r}")
        object.__setattr__(self, "plan_id", plan_id)
        if self.status not in {"selected", "idle", "composed"}:
            raise ValueError(f"invalid plan status {self.status!r}")
        object.__setattr__(
            self, "selector_id", require_ascii_id(self.selector_id, field_name="selector_id")
        )
        candidates = tuple(self.candidates)
        plugin_ids = [c.plugin_id for c in candidates]
        if len(plugin_ids) != len(set(plugin_ids)):
            raise ValueError("candidates must have unique plugin_id values")
        for candidate in candidates:
            if not isinstance(candidate, ActionProposal):
                raise TypeError("candidates must be ActionProposal")
            if candidate.frame_id != frame_id:
                raise ValueError("candidate frame_id must match plan frame_id")
            if candidate.proposal_id != f"{candidate.plugin_id}:{frame_id}":
                raise ValueError("candidate proposal_id must match plugin and frame")
        # Stable order by plugin_id
        ordered = tuple(sorted(candidates, key=lambda item: item.plugin_id))
        object.__setattr__(self, "candidates", ordered)

        contributions = tuple(self.contributions)
        by_id = {candidate.proposal_id: candidate for candidate in ordered}
        if type(self.reason) is not str:
            raise ValueError("reason must be a string")
        confidence = optional_confidence(self.confidence, field_name="confidence", clamp=False)
        if self.status == "idle":
            if self.selected_proposal_id is not None:
                raise ValueError("idle plan requires selected_proposal_id=null")
            if contributions:
                raise ValueError("idle plan requires empty contributions")
            if self.command is not None or self.reason != "" or confidence is not None:
                raise ValueError("idle plan requires command=null, reason='', confidence=null")
        elif self.status == "selected":
            if self.selected_proposal_id is None:
                raise ValueError("selected plan requires selected_proposal_id")
            if self.selected_proposal_id not in by_id:
                raise ValueError("selected_proposal_id must reference a candidate")
            if len(contributions) != 1:
                raise ValueError("selected plan requires exactly one contribution")
            contrib = contributions[0]
            selected = by_id[self.selected_proposal_id]
            if contrib.proposal_id != self.selected_proposal_id:
                raise ValueError("contribution.proposal_id must match selection")
            if contrib.plugin_id != selected.plugin_id:
                raise ValueError(
                    "contribution.plugin_id must match selected candidate plugin_id"
                )
            if contrib.role != "selected" or contrib.weight != 1.0:
                raise ValueError("contribution must be weight=1.0 role=selected")
            if selected.command is None or self.command != selected.command:
                raise ValueError("selected plan command must equal the candidate command")
            if self.reason != selected.reason or confidence != selected.confidence:
                raise ValueError("selected plan reason and confidence must equal the candidate")
        else:
            if self.selected_proposal_id is not None:
                raise ValueError("composed plan requires selected_proposal_id=null")
            if not isinstance(self.command, ProposedVehicleCommand):
                raise ValueError("composed plan requires a command")
            if not contributions:
                raise ValueError("composed plan requires contributions")
            seen: set[str] = set()
            for contrib in contributions:
                if contrib.proposal_id in seen:
                    raise ValueError("composed contributions must name distinct candidates")
                seen.add(contrib.proposal_id)
                candidate = by_id.get(contrib.proposal_id)
                if candidate is None:
                    raise ValueError("contribution must reference a candidate")
                if contrib.plugin_id != candidate.plugin_id:
                    raise ValueError("contribution.plugin_id must match the candidate")
                _finite_weight(contrib.weight)
                if type(contrib.role) is not str:
                    raise ValueError("contribution role must be a string")
        object.__setattr__(self, "contributions", contributions)
        object.__setattr__(self, "confidence", confidence)

        if type(self.metadata) is not dict:
            raise TypeError("metadata must be a dict (JSON object)")
        object.__setattr__(self, "metadata", deep_freeze(self.metadata))
        if self.schema != ACTION_PLAN_SCHEMA:
            raise ValueError(
                f"schema must be {ACTION_PLAN_SCHEMA!r}; got {self.schema!r}"
            )

    def selected_candidate(self) -> ActionProposal | None:
        if self.selected_proposal_id is None:
            return None
        for candidate in self.candidates:
            if candidate.proposal_id == self.selected_proposal_id:
                return candidate
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "plan_id": self.plan_id,
            "frame_id": self.frame_id,
            "timestamp_ms": self.timestamp_ms,
            "status": self.status,
            "command": self.command.to_dict() if self.command is not None else None,
            "reason": self.reason,
            "confidence": self.confidence,
            "selected_proposal_id": self.selected_proposal_id,
            "contributions": [item.to_dict() for item in self.contributions],
            "candidates": [item.to_dict() for item in self.candidates],
            "selector_id": self.selector_id,
            "metadata": frozen_mapping_to_dict(self.metadata),
        }


def _finite_weight(value: Any) -> float:
    if type(value) is bool or not isinstance(value, (int, float)):
        raise ValueError("contribution weight must be finite")
    weight = float(value)
    if not math.isfinite(weight):
        raise ValueError("contribution weight must be finite")
    return weight

