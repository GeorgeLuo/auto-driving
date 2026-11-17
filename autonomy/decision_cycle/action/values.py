"""The action step's decision and the authority record every action produces.

An action plugin receives the cycle context and plan (or the error that
prevented one), and returns an ``ActionDecision``: the control to apply and
whether it applies the plan's command. ``AuthorityResult`` records the plan's
command, the control the action authorized, and whether that control is the
plan's command. ``gate_id`` is the deciding action plugin's ID.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from autonomy.fields import (
    FieldError, require_ascii_id, require_choice, require_keys, require_nested,
    require_number, require_schema, require_text,
)
from autonomy.decision_cycle.errors import CYCLE_ERROR_REASONS
from autonomy.decision_cycle.plan.values import ActionPlan
from autonomy.decision_cycle.proposal.values import ProposedVehicleCommand
from autonomy.runtime.control import AutonomyControl
from autonomy.serialization import deep_freeze, frozen_mapping_to_dict

AUTHORITY_RESULT_SCHEMA = "authority_result"
COMMAND_EPS = 1e-9


@dataclass(frozen=True)
class ActionDecision:
    """Control chosen by an action plugin, and whether it applies the plan's command."""

    control: AutonomyControl
    applied: bool = False


def plan_control_metadata(plan: ActionPlan) -> dict[str, object]:
    """JSON identity carried by a plan's authorized control.

    ``proposal_ids`` and ``plugin_ids`` follow contribution order. Selected
    plans also carry ``proposal_id`` and the selected candidate's ``plugin_id``;
    composed plans have neither singular key. ``selector_id`` names the plan
    plugin and ``plan_metadata`` is its own metadata as detached, plain JSON.
    Host-generated stops and older recordings lack these keys: missing means
    unknown, never an inferred proposer or policy.
    """

    metadata: dict[str, object] = {
        "proposal_ids": [item.proposal_id for item in plan.contributions],
        "plugin_ids": [item.plugin_id for item in plan.contributions],
        "selector_id": plan.selector_id,
        "plan_metadata": frozen_mapping_to_dict(plan.metadata),
    }
    if plan.selected_proposal_id is not None:
        metadata["proposal_id"] = plan.selected_proposal_id
        metadata["plugin_id"] = plan.selected_candidate().plugin_id
    return metadata


def control_output(control: AutonomyControl) -> dict[str, object]:
    return {
        "steering": control.steering,
        "throttle": control.throttle,
        "confidence": control.confidence,
        "reason": control.reason,
    }


def proposed_equals_authorized(
    proposed: ProposedVehicleCommand | None,
    authorized: Mapping[str, object],
) -> bool:
    auth_s = float(authorized["steering"])
    auth_t = float(authorized["throttle"])
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
    authorized_output: Mapping[str, object]
    cycle_reason: str = ""
    drive_mode_gate: str | None = None
    proposed_applied: bool = False
    schema: str = AUTHORITY_RESULT_SCHEMA

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "frame_id", require_ascii_id(self.frame_id, field_name="frame_id")
        )
        require_ascii_id(self.gate_id, field_name="gate_id")
        require_choice(self.schema, (AUTHORITY_RESULT_SCHEMA,), field_name="schema")
        require_choice(self.cycle_status, ("ok", "error"), field_name="cycle_status")
        if self.cycle_status == "ok":
            if self.cycle_reason != "":
                raise FieldError("cycle_reason", "must be empty when cycle_status is ok")
        else:
            require_choice(self.cycle_reason, CYCLE_ERROR_REASONS, field_name="cycle_reason")
            if self.proposed is not None:
                raise FieldError("proposed", "must be null when cycle_status is error")
        _validate_authority_fields(self.proposed, self.drive_mode_gate, self.authorized_output)
        if type(self.proposed_applied) is not bool:
            raise FieldError("proposed_applied", "must be a bool")
        if self.proposed_applied and (
            self.proposed is None or not self.proposed_equals_authorized
        ):
            raise FieldError("proposed_applied", "requires authorized output equal to proposed")

        object.__setattr__(self, "authorized_output", deep_freeze(dict(self.authorized_output)))

    @property
    def proposed_equals_authorized(self) -> bool:
        return proposed_equals_authorized(self.proposed, self.authorized_output)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "frame_id": self.frame_id,
            "proposed": self.proposed.to_dict() if self.proposed is not None else None,
            "authorized_output": frozen_mapping_to_dict(self.authorized_output),
            "proposed_applied": self.proposed_applied,
            "proposed_equals_authorized": self.proposed_equals_authorized,
            "cycle_status": self.cycle_status,
            "cycle_reason": self.cycle_reason,
            "gate_id": self.gate_id,
            "drive_mode_gate": self.drive_mode_gate,
        }

    @classmethod
    def from_dict(cls, payload: object) -> AuthorityResult:
        document = require_schema(payload, AUTHORITY_RESULT_SCHEMA, (
            "schema", "frame_id", "proposed", "authorized_output", "proposed_applied",
            "proposed_equals_authorized", "cycle_status", "cycle_reason", "gate_id", "drive_mode_gate",
        ))
        proposed = document["proposed"]
        if document["cycle_status"] == "error":
            if proposed is not None:
                raise FieldError("proposed", "must be null when cycle_status is error")
        result = cls(
            schema=document["schema"], frame_id=document["frame_id"], gate_id=document["gate_id"],
            proposed=None if proposed is None else require_nested(
                ProposedVehicleCommand.from_dict, proposed, field_name="proposed"),
            authorized_output=document["authorized_output"], proposed_applied=document["proposed_applied"],
            cycle_status=document["cycle_status"], cycle_reason=document["cycle_reason"],
            drive_mode_gate=document["drive_mode_gate"],
        )
        equal = document["proposed_equals_authorized"]
        if type(equal) is not bool or equal != result.proposed_equals_authorized:
            raise FieldError("proposed_equals_authorized", "must agree with proposed and authorized_output")
        return result


def _validate_authority_fields(proposed: object, drive_mode_gate: object, output: object) -> None:
    """Check runtime inputs even when a caller bypasses the annotated types."""
    if proposed is not None and not isinstance(proposed, ProposedVehicleCommand):
        raise FieldError("proposed", "must be ProposedVehicleCommand or null")
    if drive_mode_gate is not None:
        require_text(drive_mode_gate, field_name="drive_mode_gate")
    document = require_keys(output, ("steering", "throttle", "confidence", "reason"), field_name="authorized_output")
    for name in ("steering", "throttle"):
        require_number(document[name], field_name=f"authorized_output.{name}", bounds=(-1, 1))
    if document["confidence"] is not None:
        require_number(document["confidence"], field_name="authorized_output.confidence", bounds=(0, 1))
    if document["reason"] != "":
        require_text(document["reason"], field_name="authorized_output.reason")


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
