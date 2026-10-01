"""The plan record the plan step produces.

``ActionPlan`` records the candidates for one cycle and, when one is
selected, that proposal and its single contribution. Its validation holds
the contribution restrictions for every plan, whichever code constructs it.
The number of candidates is the number of selected proposal plugins; each
candidate is bounded by its own proposal limits. With no candidates the plan
is idle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from autonomy.decision_cycle.proposal.values import ActionProposal
from autonomy.serialization import (
    canonical_json_size_bytes,
    deep_freeze,
    frozen_mapping_to_dict,
)
from autonomy.decision_cycle.action_identifiers import (
    plan_id_for,
    require_ascii_id,
    require_safe_int,
)

ACTION_PLAN_SCHEMA = "action_plan_v0"
# Plugin ID of the built-in plan plugin; a plan records the plugin that built it.
SELECTOR_ID = "highest_confidence"
MAX_PLAN_METADATA_BYTES = 1024


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
    selected_proposal_id: str | None = None
    contributions: tuple[PlanContribution, ...] = ()
    selector_id: str = SELECTOR_ID
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
        if self.status not in {"selected", "idle"}:
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
        if self.status == "idle":
            if self.selected_proposal_id is not None:
                raise ValueError("idle plan requires selected_proposal_id=null")
            if contributions:
                raise ValueError("idle plan requires empty contributions")
        else:
            if self.selected_proposal_id is None:
                raise ValueError("selected plan requires selected_proposal_id")
            ids = {c.proposal_id for c in ordered}
            if self.selected_proposal_id not in ids:
                raise ValueError("selected_proposal_id must reference a candidate")
            if len(contributions) != 1:
                raise ValueError("selected plan requires exactly one contribution")
            contrib = contributions[0]
            if contrib.proposal_id != self.selected_proposal_id:
                raise ValueError("contribution.proposal_id must match selection")
            selected = next(
                c for c in ordered if c.proposal_id == self.selected_proposal_id
            )
            if contrib.plugin_id != selected.plugin_id:
                raise ValueError(
                    "contribution.plugin_id must match selected candidate plugin_id"
                )
            if contrib.role != "selected" or contrib.weight != 1.0:
                raise ValueError("contribution must be weight=1.0 role=selected")
        object.__setattr__(self, "contributions", contributions)

        if type(self.metadata) is not dict:
            raise TypeError("metadata must be a dict (JSON object)")
        metadata = deep_freeze(self.metadata)
        meta_plain = frozen_mapping_to_dict(metadata)
        if canonical_json_size_bytes(meta_plain) > MAX_PLAN_METADATA_BYTES:
            raise ValueError(
                f"plan metadata exceeds {MAX_PLAN_METADATA_BYTES} bytes"
            )
        object.__setattr__(self, "metadata", metadata)
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
            "selected_proposal_id": self.selected_proposal_id,
            "contributions": [item.to_dict() for item in self.contributions],
            "candidates": [item.to_dict() for item in self.candidates],
            "selector_id": self.selector_id,
            "metadata": frozen_mapping_to_dict(self.metadata),
        }

