"""Gate contract and the authority record every gate produces.

A gate receives the cycle's plan (or the error that prevented one) and the
drive mode, and returns the control to apply. ``AuthorityResult`` records the
selected command, the control the gate authorized, and whether that control
is the selected command.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from autonomy.decision_cycle.action_identifiers import require_ascii_id
from autonomy.decision_cycle.errors import ENGINE_ERROR_REASONS
from autonomy.decision_cycle.planning.values import ActionPlan
from autonomy.decision_cycle.proposal.inputs import ComponentEnvelope, unavailable_envelope
from autonomy.decision_cycle.proposal.values import ProposedVehicleCommand
from autonomy.runtime.engine import AutonomyControl

AUTHORITY_RESULT_SCHEMA = "authority_result_v0"
COMMAND_EPS = 1e-9


@dataclass(frozen=True)
class GateDecision:
    """Control chosen by a gate, and whether it applies the selected command."""

    control: AutonomyControl
    applied: bool = False


class ActionGate(Protocol):
    gate_id: str

    def decide(
        self,
        plan: ActionPlan | None,
        *,
        mode: str,
        error_reason: str | None = None,
    ) -> GateDecision:
        """Return the control for this cycle. ``plan`` is None exactly when ``error_reason`` is set."""


def control_output(control: AutonomyControl) -> dict[str, Any]:
    return {
        "steering": control.steering,
        "throttle": control.throttle,
        "confidence": control.confidence,
        "reason": control.reason,
    }


def proposed_equals_authorized(
    proposed: ProposedVehicleCommand | None,
    authorized: dict[str, Any],
) -> bool:
    auth_s = float(authorized.get("steering", 0.0))
    auth_t = float(authorized.get("throttle", 0.0))
    if proposed is None:
        return abs(auth_s) < COMMAND_EPS and abs(auth_t) < COMMAND_EPS
    return (
        abs(proposed.steering - auth_s) < COMMAND_EPS
        and abs(proposed.throttle - auth_t) < COMMAND_EPS
    )


@dataclass(frozen=True)
class AuthorityResult:
    frame_id: str
    gate_id: str
    proposed: ProposedVehicleCommand | None
    cycle_status: str
    authorized_output: dict[str, Any]
    cycle_reason: str = ""
    host_application: ComponentEnvelope = field(
        default_factory=lambda: unavailable_envelope("host_did_not_report_application")
    )
    drive_mode_gate: str = "unknown"
    proposed_applied: bool = False
    schema: str = AUTHORITY_RESULT_SCHEMA

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "frame_id", require_ascii_id(self.frame_id, field_name="frame_id")
        )
        require_ascii_id(self.gate_id, field_name="gate_id")
        if self.schema != AUTHORITY_RESULT_SCHEMA:
            raise ValueError(
                f"schema must be {AUTHORITY_RESULT_SCHEMA!r}; got {self.schema!r}"
            )
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
        output = self.authorized_output
        if not isinstance(output, dict) or set(output) != {
            "steering",
            "throttle",
            "confidence",
            "reason",
        }:
            raise ValueError(
                "authorized_output must hold steering, throttle, confidence, and reason"
            )
        if type(self.proposed_applied) is not bool:
            raise TypeError("proposed_applied must be a bool")
        if self.proposed_applied and (
            self.proposed is None or not self.proposed_equals_authorized
        ):
            raise ValueError("proposed_applied requires authorized output equal to proposed")

    @property
    def proposed_equals_authorized(self) -> bool:
        return proposed_equals_authorized(self.proposed, self.authorized_output)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "frame_id": self.frame_id,
            "proposed": self.proposed.to_dict() if self.proposed is not None else None,
            "authorized_output": dict(self.authorized_output),
            "proposed_applied": self.proposed_applied,
            "host_application": self.host_application.to_dict(),
            "proposed_equals_authorized": self.proposed_equals_authorized,
            "cycle_status": self.cycle_status,
            "cycle_reason": self.cycle_reason,
            "gate_id": self.gate_id,
            "drive_mode_gate": self.drive_mode_gate,
        }


def build_authority(
    *,
    frame_id: str,
    gate_id: str,
    decision: GateDecision,
    cycle_status: str,
    cycle_reason: str = "",
    proposed: ProposedVehicleCommand | None = None,
    host_application: ComponentEnvelope | None = None,
    drive_mode_gate: str = "unknown",
) -> AuthorityResult:
    # Detach proposed command so authority does not alias plan candidate storage.
    detached_proposed: ProposedVehicleCommand | None = None
    if proposed is not None:
        if not isinstance(proposed, ProposedVehicleCommand):
            raise TypeError("proposed must be ProposedVehicleCommand or None")
        detached_proposed = ProposedVehicleCommand.from_dict(proposed.to_dict())
    return AuthorityResult(
        frame_id=frame_id,
        gate_id=gate_id,
        proposed=detached_proposed,
        cycle_status=cycle_status,
        cycle_reason=cycle_reason,
        authorized_output=control_output(decision.control),
        host_application=host_application
        or unavailable_envelope("host_did_not_report_application"),
        drive_mode_gate=drive_mode_gate,
        proposed_applied=decision.applied,
    )
