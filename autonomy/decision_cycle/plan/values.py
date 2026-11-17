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

from dataclasses import dataclass, field, fields
from typing import Any

from autonomy.decision_cycle.proposal.values import ActionProposal, ProposedVehicleCommand
from autonomy.serialization import (
    deep_freeze,
    frozen_mapping_to_dict,
)
from autonomy.decision_cycle.action_identifiers import plan_id_for
from autonomy.fields import (
    FieldError, require_ascii_id, require_choice, require_keys, require_nested,
    require_number, require_safe_int, require_schema, require_text,
)

ACTION_PLAN_SCHEMA = "action_plan"


@dataclass(frozen=True)
class PlanContribution:
    proposal_id: str
    plugin_id: str
    weight: float = 1.0
    role: str = "selected"

    def __post_init__(self) -> None:
        proposal_id = require_text(self.proposal_id, field_name="proposal_id")
        plugin_id = require_ascii_id(self.plugin_id, field_name="plugin_id")
        prefix = f"{plugin_id}:"
        if not proposal_id.startswith(prefix):
            raise FieldError("proposal_id", "must name plugin_id followed by the frame_id")
        require_ascii_id(proposal_id[len(prefix):], field_name="proposal_id")
        require_number(self.weight, field_name="weight")
        require_text(self.role, field_name="role")

    @classmethod
    def from_dict(cls, payload: object) -> PlanContribution:
        document = require_keys(payload, {item.name for item in fields(cls)})
        return cls(proposal_id=document["proposal_id"], plugin_id=document["plugin_id"],
                   weight=document["weight"], role=document["role"])

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
            raise FieldError("plan_id", f"must be {plan_id_for(frame_id)!r}")
        object.__setattr__(self, "plan_id", plan_id)
        require_choice(self.status, ("selected", "idle", "composed"), field_name="status")
        object.__setattr__(
            self, "selector_id", require_ascii_id(self.selector_id, field_name="selector_id")
        )
        object.__setattr__(self, "candidates", self._ordered_candidates())

        if not isinstance(self.contributions, (list, tuple)):  # pyright: ignore[reportUnnecessaryIsInstance] - validate runtime callers
            raise FieldError("contributions", "must be an array")
        contributions = tuple(self.contributions)
        if not all(isinstance(item, PlanContribution) for item in contributions):  # pyright: ignore[reportUnnecessaryIsInstance] - validate runtime callers
            raise FieldError("contributions", "must contain PlanContribution values")
        if self.reason != "":
            require_text(self.reason, field_name="reason")
        confidence = self.confidence
        if confidence is not None:
            confidence = require_number(confidence, field_name="confidence", bounds=(0, 1))
        if self.selected_proposal_id is not None:
            require_text(self.selected_proposal_id, field_name="selected_proposal_id")
        object.__setattr__(self, "contributions", contributions)
        object.__setattr__(self, "confidence", confidence)

        if type(self.metadata) is not dict:
            raise FieldError("metadata", "must be a JSON object")
        try:
            object.__setattr__(self, "metadata", deep_freeze(self.metadata, field_name="metadata"))
        except ValueError as exc:
            raise FieldError("metadata", str(exc)) from exc
        require_choice(self.schema, (ACTION_PLAN_SCHEMA,), field_name="schema")
        if self.status == "idle":
            self._validate_idle()
        elif self.status == "selected":
            self._validate_selected()
        else:
            self._validate_composed()

    def _ordered_candidates(self) -> tuple[ActionProposal, ...]:
        if not isinstance(self.candidates, (list, tuple)):  # pyright: ignore[reportUnnecessaryIsInstance] - validate runtime callers
            raise FieldError("candidates", "must be an array")
        candidates = tuple(self.candidates)
        if not all(isinstance(candidate, ActionProposal) for candidate in candidates):  # pyright: ignore[reportUnnecessaryIsInstance] - validate runtime callers
            raise FieldError("candidates", "must contain ActionProposal values")
        plugin_ids = [c.plugin_id for c in candidates]
        if len(plugin_ids) != len(set(plugin_ids)):
            raise FieldError("candidates", "must have unique plugin_id values")
        for candidate in candidates:
            if candidate.frame_id != self.frame_id:
                raise FieldError("candidates", "frame_id must match plan frame_id")
            if candidate.proposal_id != f"{candidate.plugin_id}:{self.frame_id}":
                raise FieldError("candidates", "proposal_id must match plugin and frame")
        return tuple(sorted(candidates, key=lambda item: item.plugin_id))

    def _validate_idle(self) -> None:
        if self.selected_proposal_id is not None:
            raise FieldError("selected_proposal_id", "must be null for an idle plan")
        if self.contributions:
            raise FieldError("contributions", "must be empty for an idle plan")
        if self.command is not None:
            raise FieldError("command", "must be null for an idle plan")
        if self.reason != "":
            raise FieldError("reason", "must be empty for an idle plan")
        if self.confidence is not None:
            raise FieldError("confidence", "must be null for an idle plan")

    def _validate_selected(self) -> None:
        by_id = {candidate.proposal_id: candidate for candidate in self.candidates}
        if self.selected_proposal_id is None:
            raise FieldError("selected_proposal_id", "is required for a selected plan")
        if self.selected_proposal_id not in by_id:
            raise FieldError("selected_proposal_id", "must reference a candidate")
        if len(self.contributions) != 1:
            raise FieldError("contributions", "must contain exactly one contribution for a selected plan")
        contrib = self.contributions[0]
        selected = by_id[self.selected_proposal_id]
        if contrib.proposal_id != self.selected_proposal_id:
            raise FieldError("contributions.0.proposal_id", "must match selection")
        if contrib.plugin_id != selected.plugin_id:
            raise FieldError("contributions.0.plugin_id", "must match selected candidate plugin_id")
        if contrib.role != "selected" or contrib.weight != 1.0:
            raise FieldError("contributions", "must be weight=1.0 role=selected")
        self._validate_selected_command(selected)

    def _validate_selected_command(self, selected: ActionProposal) -> None:
        if not isinstance(self.command, ProposedVehicleCommand) or selected.command is None:
            raise FieldError("command", "must equal the selected candidate command")
        for name in ("steering", "throttle"):
            if getattr(self.command, name) != getattr(selected.command, name):
                raise FieldError(f"command.{name}", "must equal the selected candidate command")
        if self.reason != selected.reason:
            raise FieldError("reason", "must equal the selected candidate reason")
        if self.confidence != selected.confidence:
            raise FieldError("confidence", "must equal the selected candidate confidence")

    def _validate_composed(self) -> None:
        by_id = {candidate.proposal_id: candidate for candidate in self.candidates}
        if self.selected_proposal_id is not None:
            raise FieldError("selected_proposal_id", "must be null for a composed plan")
        if not isinstance(self.command, ProposedVehicleCommand):
            raise FieldError("command", "is required for a composed plan")
        if not self.contributions:
            raise FieldError("contributions", "are required for a composed plan")
        seen: set[str] = set()
        for contrib in self.contributions:
            if contrib.proposal_id in seen:
                raise FieldError("contributions", "must name distinct candidates")
            seen.add(contrib.proposal_id)
            candidate = by_id.get(contrib.proposal_id)
            if candidate is None:
                raise FieldError("contributions", "must reference a candidate")
            if contrib.plugin_id != candidate.plugin_id:
                raise FieldError("contributions", "plugin_id must match the candidate")

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

    @classmethod
    def from_dict(cls, payload: object) -> ActionPlan:
        document = require_schema(payload, ACTION_PLAN_SCHEMA, {item.name for item in fields(cls)})
        parsed = {}
        for name, parse in (("candidates", ActionProposal.from_dict), ("contributions", PlanContribution.from_dict)):
            values = document[name]
            if not isinstance(values, (list, tuple)):
                raise FieldError(name, "must be an array")
            parsed[name] = tuple(require_nested(parse, item, field_name=f"{name}.{index}")
                                 for index, item in enumerate(values))
        candidates = parsed["candidates"]
        if tuple(sorted(candidates, key=lambda item: item.plugin_id)) != candidates:
            raise FieldError("candidates", "must be ordered by plugin_id")
        command = document["command"]
        if document["status"] == "idle" and command is not None:
            raise FieldError("command", "must be null for an idle plan")
        return cls(
            schema=document["schema"], plan_id=require_text(document["plan_id"], field_name="plan_id"),
            frame_id=document["frame_id"], timestamp_ms=document["timestamp_ms"], status=document["status"],
            selector_id=document["selector_id"], selected_proposal_id=document["selected_proposal_id"],
            command=None if command is None else require_nested(
                ProposedVehicleCommand.from_dict, command, field_name="command"),
            reason=document["reason"], confidence=document["confidence"], metadata=document["metadata"], **parsed,
        )
