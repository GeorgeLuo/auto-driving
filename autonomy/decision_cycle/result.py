"""Aggregate result of the action composition for one cycle.

``ActionResult`` joins the proposal input, the plan, and the gate's authority
record, and keeps their status and frame fields consistent. ``control`` is the
control the gate authorized; it is not serialized separately because
``authority.authorized_output`` records it. The outer cycle result is
``DecisionCycleResult`` in ``autonomy.decision_cycle.cycle``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from autonomy.decision_cycle.action_gate.values import AuthorityResult
from autonomy.decision_cycle.action_identifiers import require_ascii_id
from autonomy.decision_cycle.errors import ENGINE_ERROR_REASONS
from autonomy.decision_cycle.planning.values import ActionPlan
from autonomy.decision_cycle.proposal.inputs import DecisionDataSource
from autonomy.runtime.engine import AutonomyControl

ACTION_RESULT_SCHEMA = "action_result_v0"


@dataclass(frozen=True)
class ActionResult:
    frame_id: str
    status: str
    authority: AuthorityResult
    reason: str = ""
    source: DecisionDataSource | None = None
    plan: ActionPlan | None = None
    control: AutonomyControl | None = None
    schema: str = ACTION_RESULT_SCHEMA

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "frame_id", require_ascii_id(self.frame_id, field_name="frame_id")
        )
        if self.schema != ACTION_RESULT_SCHEMA:
            raise ValueError(
                f"schema must be {ACTION_RESULT_SCHEMA!r}; got {self.schema!r}"
            )
        if self.status not in {"ok", "engine_error"}:
            raise ValueError(f"invalid cycle status {self.status!r}")
        if not isinstance(self.authority, AuthorityResult):
            raise TypeError("authority must be AuthorityResult")
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
        if self.authority.frame_id != self.frame_id:
            raise ValueError("authority.frame_id must match cycle frame_id")
        if self.control is None:
            object.__setattr__(
                self, "control", AutonomyControl(**self.authority.authorized_output)
            )
        elif not isinstance(self.control, AutonomyControl):
            raise TypeError("control must be AutonomyControl")
        elif {
            "steering": self.control.steering,
            "throttle": self.control.throttle,
            "confidence": self.control.confidence,
            "reason": self.control.reason,
        } != self.authority.authorized_output:
            raise ValueError("control must match authority.authorized_output")

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
