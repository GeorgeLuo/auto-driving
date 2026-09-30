"""Hold action gate.

The hold policy records the proposed command and always permits idle
control: its authorized output is idle and the proposal is never applied.
``ShadowAuthorityResult`` is its gate result; ``HoldGateResult`` names the
same class.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from autonomy.decision_cycle.action_identifiers import require_ascii_id
from autonomy.decision_cycle.errors import ENGINE_ERROR_REASONS
from autonomy.decision_cycle.proposal.inputs import ComponentEnvelope, unavailable_envelope
from autonomy.decision_cycle.proposal.values import ProposedVehicleCommand
from autonomy.runtime.engine import AutonomyControl

SHADOW_AUTHORITY_RESULT_SCHEMA = "shadow_authority_result_v0"
AUTHORIZED_IDLE_REASON = "shadow-only-idle"
COMMAND_EPS = 1e-9


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
        if self.schema != SHADOW_AUTHORITY_RESULT_SCHEMA:
            raise ValueError(
                f"schema must be {SHADOW_AUTHORITY_RESULT_SCHEMA!r}; got {self.schema!r}"
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


HoldGateResult = ShadowAuthorityResult


def build_authority(
    *,
    frame_id: str,
    cycle_status: str,
    cycle_reason: str = "",
    proposed: ProposedVehicleCommand | None = None,
    host_application: ComponentEnvelope | None = None,
    drive_mode_gate: str = "unknown",
) -> ShadowAuthorityResult:
    # Detach proposed command so authority does not alias plan candidate storage.
    detached_proposed: ProposedVehicleCommand | None = None
    if proposed is not None:
        if not isinstance(proposed, ProposedVehicleCommand):
            raise TypeError("proposed must be ProposedVehicleCommand or None")
        detached_proposed = ProposedVehicleCommand.from_dict(proposed.to_dict())
    return ShadowAuthorityResult(
        frame_id=frame_id,
        proposed=detached_proposed,
        cycle_status=cycle_status,
        cycle_reason=cycle_reason,
        host_application=host_application
        or unavailable_envelope("host_did_not_report_application"),
        drive_mode_gate=drive_mode_gate,
    )
