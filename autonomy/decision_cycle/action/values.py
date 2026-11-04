"""The action step's decision and the authority record every action produces.

An action plugin receives the cycle context and plan (or the error that
prevented one), and returns an ``ActionDecision``: the control to apply and
whether it applies the plan's command. ``AuthorityResult`` records the plan's
command, the control the action authorized, and whether that control is the
plan's command. ``gate_id`` is the deciding action plugin's ID.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from autonomy.decision_cycle.action_identifiers import require_ascii_id
from autonomy.decision_cycle.errors import CYCLE_ERROR_REASONS
from autonomy.decision_cycle.plan.values import ActionPlan
from autonomy.decision_cycle.proposal.values import ProposedVehicleCommand
from autonomy.runtime.control import AutonomyControl
from autonomy.serialization import require_canonical_export, require_exact_keys

AUTHORITY_RESULT_SCHEMA = "authority_result"
AUTHORITY_EXACT_KEYS = frozenset(
    {
        "schema",
        "frame_id",
        "proposed",
        "authorized_output",
        "proposed_applied",
        "proposed_equals_authorized",
        "cycle_status",
        "cycle_reason",
        "gate_id",
        "drive_mode_gate",
    }
)
COMMAND_EPS = 1e-9


@dataclass(frozen=True)
class ActionDecision:
    """Control chosen by an action plugin, and whether it applies the plan's command."""

    control: AutonomyControl
    applied: bool = False


def plan_control_metadata(plan: ActionPlan) -> dict[str, Any]:
    """The proposals behind an applied plan: every contribution, and the selected one."""

    metadata: dict[str, Any] = {
        "proposal_ids": [item.proposal_id for item in plan.contributions],
    }
    if plan.selected_proposal_id is not None:
        metadata["proposal_id"] = plan.selected_proposal_id
    return metadata


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
    drive_mode_gate: str | None = None
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
        if self.cycle_status not in {"ok", "error"}:
            raise ValueError(f"invalid cycle_status {self.cycle_status!r}")
        if self.cycle_status == "ok":
            if self.cycle_reason != "":
                raise ValueError("ok cycle_reason must be empty")
        else:
            if self.cycle_reason not in CYCLE_ERROR_REASONS:
                raise ValueError(f"unknown cycle error reason {self.cycle_reason!r}")
            if self.proposed is not None:
                raise ValueError("error requires proposed=null")
        if self.proposed is not None and not isinstance(
            self.proposed, ProposedVehicleCommand
        ):
            raise TypeError("proposed must be ProposedVehicleCommand or None")
        if self.drive_mode_gate is not None and not isinstance(self.drive_mode_gate, str):
            raise TypeError("drive_mode_gate must be a string or None")
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
            "proposed_equals_authorized": self.proposed_equals_authorized,
            "cycle_status": self.cycle_status,
            "cycle_reason": self.cycle_reason,
            "gate_id": self.gate_id,
            "drive_mode_gate": self.drive_mode_gate,
        }

    @classmethod
    def decode(cls, payload: object, *, field: str = "cycle.action.authority") -> "AuthorityResult":
        """The authority record, when ``payload`` is exactly its export."""

        if not isinstance(payload, dict):
            raise ValueError(f"{field} must be an AuthorityResult object.")
        if payload.get("schema") != AUTHORITY_RESULT_SCHEMA:
            raise ValueError(f"{field}.schema must be {AUTHORITY_RESULT_SCHEMA!r}.")
        require_exact_keys(payload, AUTHORITY_EXACT_KEYS, field=field)
        gate_id = payload.get("gate_id")
        try:
            require_ascii_id(gate_id, field_name="gate_id")
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field}.gate_id must be an action plugin id: {exc}") from exc
        if type(payload.get("proposed_applied")) is not bool:
            raise ValueError(f"{field}.proposed_applied must be a bool.")
        authorized_output = payload.get("authorized_output")
        # Imported here: the hold plugin imports this module's decision type.
        from autonomy.decision_cycle.action.hold import HoldAction, idle_output

        if gate_id == HoldAction.plugin_id:
            if payload.get("proposed_applied") is not False:
                raise ValueError(f"{field}.proposed_applied must be false for the hold gate.")
            require_canonical_export(
                authorized_output,
                idle_output(),
                field=f"{field}.authorized_output",
            )
        else:
            try:
                control = AutonomyControl(**authorized_output)
            except TypeError as exc:
                raise ValueError(
                    f"{field}.authorized_output is not a control output: {exc}"
                ) from exc
            require_canonical_export(
                authorized_output,
                control_output(control),
                field=f"{field}.authorized_output",
            )
        proposed_payload = payload.get("proposed")
        proposed = (
            None
            if proposed_payload is None
            else ProposedVehicleCommand.decode(proposed_payload, field=f"{field}.proposed")
        )
        try:
            authority = cls(
                frame_id=str(payload["frame_id"]),
                gate_id=str(gate_id),
                proposed=proposed,
                cycle_status=str(payload["cycle_status"]),
                cycle_reason=str(payload.get("cycle_reason") or ""),
                authorized_output=dict(authorized_output),
                drive_mode_gate=payload.get("drive_mode_gate"),
                proposed_applied=payload["proposed_applied"],
                schema=str(payload.get("schema") or AUTHORITY_RESULT_SCHEMA),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field} failed AuthorityResult construction: {exc}") from exc
        require_canonical_export(payload, authority.to_dict(), field=field)
        return authority


def build_authority(
    *,
    frame_id: str,
    gate_id: str,
    decision: ActionDecision,
    cycle_status: str,
    cycle_reason: str = "",
    proposed: ProposedVehicleCommand | None = None,
    drive_mode_gate: str | None = None,
) -> AuthorityResult:
    return AuthorityResult(
        frame_id=frame_id,
        gate_id=gate_id,
        proposed=proposed,
        cycle_status=cycle_status,
        cycle_reason=cycle_reason,
        authorized_output=control_output(decision.control),
        drive_mode_gate=drive_mode_gate,
        proposed_applied=decision.applied,
    )
