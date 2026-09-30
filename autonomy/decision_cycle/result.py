"""Aggregate result of the action composition for one cycle.

``ShadowDecisionCycleResult`` joins the proposal input, the plan, and the
hold gate result, and keeps their status and frame fields consistent. The
outer cycle result is ``DecisionCycleResult`` in ``autonomy.decision.cycle``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from autonomy.decision_cycle.action_gate.hold import ShadowAuthorityResult
from autonomy.decision_cycle.action_identifiers import require_ascii_id
from autonomy.decision_cycle.errors import ENGINE_ERROR_REASONS
from autonomy.decision_cycle.planning.values import ActionPlan
from autonomy.decision_cycle.proposal.inputs import DecisionDataSource

SHADOW_DECISION_CYCLE_RESULT_SCHEMA = "shadow_decision_cycle_result_v0"


@dataclass(frozen=True)
class ShadowDecisionCycleResult:
    frame_id: str
    status: str
    authority: ShadowAuthorityResult
    reason: str = ""
    source: DecisionDataSource | None = None
    plan: ActionPlan | None = None
    schema: str = SHADOW_DECISION_CYCLE_RESULT_SCHEMA

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "frame_id", require_ascii_id(self.frame_id, field_name="frame_id")
        )
        if self.schema != SHADOW_DECISION_CYCLE_RESULT_SCHEMA:
            raise ValueError(
                f"schema must be {SHADOW_DECISION_CYCLE_RESULT_SCHEMA!r}; "
                f"got {self.schema!r}"
            )
        if self.status not in {"ok", "engine_error"}:
            raise ValueError(f"invalid cycle status {self.status!r}")
        if self.status == "ok":
            if self.reason != "":
                raise ValueError("ok reason must be empty")
            if self.plan is None:
                raise ValueError("ok cycle requires a plan")
            if self.authority.cycle_status != "ok" or self.authority.cycle_reason != "":
                raise ValueError("authority cycle fields must match ok status")
        else:
            if self.reason not in ENGINE_ERROR_REASONS:
                raise ValueError(f"unknown engine_error reason {self.reason!r}")
            if self.plan is not None:
                raise ValueError("engine_error requires plan=null")
            if (
                self.authority.cycle_status != "engine_error"
                or self.authority.cycle_reason != self.reason
            ):
                raise ValueError("authority cycle fields must match engine_error")
        if not isinstance(self.authority, ShadowAuthorityResult):
            raise TypeError("authority must be ShadowAuthorityResult")
        if self.authority.frame_id != self.frame_id:
            raise ValueError("authority.frame_id must match cycle frame_id")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "frame_id": self.frame_id,
            "status": self.status,
            "reason": self.reason,
            "source": self.source.to_dict() if self.source is not None else None,
            "plan": self.plan.to_dict() if self.plan is not None else None,
            "authority": self.authority.to_dict(),
        }
