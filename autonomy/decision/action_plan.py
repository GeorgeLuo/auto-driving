"""ActionPlan and deterministic_first_active selector (M006-03)."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from autonomy.decision.action_proposal import ActionProposal
from autonomy.decision.memory import canonical_json_bytes
from autonomy.decision.shadow_ids import (
    deep_freeze,
    frozen_mapping_to_dict,
    plan_id_for,
    require_ascii_id,
    require_safe_int,
)

ACTION_PLAN_SCHEMA = "action_plan_v0"
SELECTOR_ID = "deterministic_first_active"
MAX_PLAN_METADATA_BYTES = 1024
MAX_PLAN_BYTES = 24_576
MAX_CANDIDATES = 4


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
        if self.selector_id != SELECTOR_ID:
            raise ValueError(f"selector_id must be {SELECTOR_ID}")
        candidates = tuple(self.candidates)
        if not 1 <= len(candidates) <= MAX_CANDIDATES:
            raise ValueError("candidates count must be in 1..4")
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

        metadata = deep_freeze(dict(self.metadata))
        meta_plain = frozen_mapping_to_dict(metadata)
        if canonical_json_bytes(meta_plain) > MAX_PLAN_METADATA_BYTES:
            raise ValueError(
                f"plan metadata exceeds {MAX_PLAN_METADATA_BYTES} bytes"
            )
        object.__setattr__(self, "metadata", metadata)
        if self.schema != ACTION_PLAN_SCHEMA:
            raise ValueError(
                f"schema must be {ACTION_PLAN_SCHEMA!r}; got {self.schema!r}"
            )
        size = canonical_json_bytes(self.to_dict())
        if size > MAX_PLAN_BYTES:
            raise ValueError(
                f"ActionPlan serializes to {size} bytes; max {MAX_PLAN_BYTES}"
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


def select_action_plan(
    *,
    frame_id: str,
    timestamp_ms: int,
    candidates: list[ActionProposal] | tuple[ActionProposal, ...],
    metadata: dict[str, Any] | None = None,
) -> ActionPlan:
    """deterministic_first_active selector."""

    candidates_list = list(candidates)
    active = [
        item
        for item in candidates_list
        if item.lifecycle in {"fresh", "retained"}
        and item.freshness == item.lifecycle
        and item.available
        and item.command is not None
        and item.proposal_id.endswith(f":{frame_id}")
    ]
    if not active:
        return ActionPlan(
            frame_id=frame_id,
            timestamp_ms=timestamp_ms,
            status="idle",
            candidates=tuple(candidates_list),
            selected_proposal_id=None,
            contributions=(),
            metadata=dict(metadata or {}),
        )
    active.sort(key=lambda item: (-item.confidence, item.plugin_id))
    selected = active[0]
    return ActionPlan(
        frame_id=frame_id,
        timestamp_ms=timestamp_ms,
        status="selected",
        candidates=tuple(candidates_list),
        selected_proposal_id=selected.proposal_id,
        contributions=(
            PlanContribution(
                proposal_id=selected.proposal_id,
                plugin_id=selected.plugin_id,
                weight=1.0,
                role="selected",
            ),
        ),
        metadata=dict(metadata or {}),
    )
