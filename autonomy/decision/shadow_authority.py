"""Shadow authority and cycle result contracts (M006-03)."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from autonomy.decision.action_plan import ActionPlan
from autonomy.decision.action_proposal import ProposedVehicleCommand
from autonomy.decision.decision_data import (
    ComponentEnvelope,
    DecisionDataSource,
    unavailable_envelope,
)
from autonomy.decision.shadow_ids import require_ascii_id
from autonomy.runtime.engine import AutonomyControl

SHADOW_AUTHORITY_RESULT_SCHEMA = "shadow_authority_result_v0"
SHADOW_DECISION_CYCLE_RESULT_SCHEMA = "shadow_decision_cycle_result_v0"
AUTHORIZED_IDLE_REASON = "shadow-only-idle"
COMMAND_EPS = 1e-9

ENGINE_ERROR_REASONS = frozenset(
    {
        "decision_data_source_invalid",
        "action_plan_invariant_violated",
        "action_proposal_matrix_violated",
        "synthetic_error_proposal_failed",
        "engine_internal_error",
    }
)


def authorized_idle_output() -> dict[str, Any]:
    return {
        "steering": 0.0,
        "throttle": 0.0,
        "confidence": 1.0,
        "reason": AUTHORIZED_IDLE_REASON,
    }


def authorized_idle_control() -> AutonomyControl:
    return AutonomyControl(
        steering=0.0,
        throttle=0.0,
        confidence=1.0,
        reason=AUTHORIZED_IDLE_REASON,
    )


def proposed_equals_authorized(
    proposed: ProposedVehicleCommand | None,
    authorized: dict[str, Any] | None = None,
) -> bool:
    authorized = authorized or authorized_idle_output()
    auth_s = float(authorized.get("steering", 0.0))
    auth_t = float(authorized.get("throttle", 0.0))
    if proposed is None:
        return abs(auth_s) < COMMAND_EPS and abs(auth_t) < COMMAND_EPS
    return (
        abs(proposed.steering - auth_s) < COMMAND_EPS
        and abs(proposed.throttle - auth_t) < COMMAND_EPS
    )


@dataclass(frozen=True)
class ShadowAuthorityResult:
    frame_id: str
    proposed: ProposedVehicleCommand | None
    cycle_status: str
    cycle_reason: str = ""
    host_application: ComponentEnvelope = field(
        default_factory=lambda: unavailable_envelope("host_did_not_report_application")
    )
    drive_mode_gate: str = "unknown"
    authority_mode: str = "shadow_only"
    proposed_applied: bool = False
    schema: str = SHADOW_AUTHORITY_RESULT_SCHEMA

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "frame_id", require_ascii_id(self.frame_id, field_name="frame_id")
        )
        if self.authority_mode != "shadow_only":
            raise ValueError("authority_mode must be shadow_only")
        if self.proposed_applied is not False:
            raise ValueError("proposed_applied must be false for shadow-proposals")
        if self.cycle_status not in {"ok", "engine_error"}:
            raise ValueError(f"invalid cycle_status {self.cycle_status!r}")
        if self.cycle_status == "ok":
            if self.cycle_reason != "":
                raise ValueError("ok cycle_reason must be empty")
        else:
            if self.cycle_reason not in ENGINE_ERROR_REASONS:
                raise ValueError(f"unknown engine_error reason {self.cycle_reason!r}")
            if self.proposed is not None:
                raise ValueError("engine_error requires proposed=null")
        if self.proposed is not None and not isinstance(
            self.proposed, ProposedVehicleCommand
        ):
            raise TypeError("proposed must be ProposedVehicleCommand or None")
        if not isinstance(self.host_application, ComponentEnvelope):
            raise TypeError("host_application must be ComponentEnvelope")

    @property
    def authorized_output(self) -> dict[str, Any]:
        return authorized_idle_output()

    @property
    def proposed_equals_authorized(self) -> bool:
        return proposed_equals_authorized(self.proposed, self.authorized_output)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "frame_id": self.frame_id,
            "proposed": self.proposed.to_dict() if self.proposed is not None else None,
            "authorized_output": self.authorized_output,
            "proposed_applied": False,
            "host_application": self.host_application.to_dict(),
            "proposed_equals_authorized": self.proposed_equals_authorized,
            "cycle_status": self.cycle_status,
            "cycle_reason": self.cycle_reason,
            "authority_mode": self.authority_mode,
            "drive_mode_gate": self.drive_mode_gate,
        }


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


def build_authority(
    *,
    frame_id: str,
    cycle_status: str,
    cycle_reason: str = "",
    proposed: ProposedVehicleCommand | None = None,
    host_application: ComponentEnvelope | None = None,
    drive_mode_gate: str = "unknown",
) -> ShadowAuthorityResult:
    return ShadowAuthorityResult(
        frame_id=frame_id,
        proposed=proposed,
        cycle_status=cycle_status,
        cycle_reason=cycle_reason,
        host_application=host_application
        or unavailable_envelope("host_did_not_report_application"),
        drive_mode_gate=drive_mode_gate,
    )
