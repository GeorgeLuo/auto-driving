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
from autonomy.decision_cycle.action_identifiers import require_ascii_id
from autonomy.decision_cycle.errors import CYCLE_ERROR_REASONS
from autonomy.runtime.control import AutonomyControl

ACTION_RESULT_SCHEMA = "action_result_v1"


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
        if self.schema != ACTION_RESULT_SCHEMA:
            raise ValueError(
                f"schema must be {ACTION_RESULT_SCHEMA!r}; got {self.schema!r}"
            )
        if self.status not in {"ok", "error"}:
            raise ValueError(f"invalid cycle status {self.status!r}")
        if not isinstance(self.authority, AuthorityResult):
            raise TypeError("authority must be AuthorityResult")
        if self.status == "ok":
            if self.reason != "":
                raise ValueError("ok reason must be empty")
        elif self.reason not in CYCLE_ERROR_REASONS:
            raise ValueError(f"unknown cycle error reason {self.reason!r}")
        if (
            self.authority.cycle_status != self.status
            or self.authority.cycle_reason != self.reason
        ):
            raise ValueError("authority cycle fields must match the action status")
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
            "authority": self.authority.to_dict(),
        }
