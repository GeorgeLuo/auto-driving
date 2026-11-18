"""The action step's record for one cycle.

``ActionResult`` holds the action plugin's authority record and the control it
authorized. Status ``error`` carries the reason the cycle could not plan; the
action plugin still chose the control. ``control`` is not serialized
separately because ``authority.authorized_output`` records it. The proposal
and plan are recorded by their own steps in ``DecisionCycleResult``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from autonomy.decision_cycle.action.values import AuthorityResult
from autonomy.fields import FieldError, require_ascii_id, require_choice, require_nested, require_schema
from autonomy.decision_cycle.errors import CYCLE_ERROR_REASONS
from autonomy.runtime.control import AutonomyControl

ACTION_RESULT_SCHEMA = "action_result"


@dataclass(frozen=True)
class ActionResult:
    frame_id: str
    status: str
    authority: AuthorityResult
    reason: str = ""
    control: AutonomyControl | None = None
    schema: str = ACTION_RESULT_SCHEMA

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "frame_id", require_ascii_id(self.frame_id, field_name="frame_id")
        )
        require_choice(self.schema, (ACTION_RESULT_SCHEMA,), field_name="schema")
        require_choice(self.status, ("ok", "error"), field_name="status")
        if not isinstance(self.authority, AuthorityResult):
            raise FieldError("authority", "must be AuthorityResult")
        if self.status == "ok":
            if self.reason != "":
                raise FieldError("reason", "must be empty when status is ok")
        else:
            require_choice(self.reason, CYCLE_ERROR_REASONS, field_name="reason")
        if (
            self.authority.cycle_status != self.status
            or self.authority.cycle_reason != self.reason
        ):
            raise FieldError("authority", "cycle fields must match the action status and reason")
        if self.authority.frame_id != self.frame_id:
            raise FieldError("authority.frame_id", "must match cycle frame_id")
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
            "authority": self.authority.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: object) -> ActionResult:
        document = require_schema(payload, ACTION_RESULT_SCHEMA, ("schema", "frame_id", "status", "reason", "authority"))
        return cls(
            schema=document["schema"], frame_id=document["frame_id"], status=document["status"], reason=document["reason"],
            authority=require_nested(AuthorityResult.from_dict, document["authority"], field_name="authority"),
        )
