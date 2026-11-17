"""The proposal step's record for one cycle.

``ProposalResult`` holds one admitted
candidate per selected plugin. Status ``error`` records why the step could not
produce candidates; the plan step then does not plan and the action step
fails closed.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any

from autonomy.fields import FieldError, require_ascii_id, require_choice, require_nested, require_schema
from autonomy.decision_cycle.errors import CYCLE_ERROR_REASONS
from autonomy.decision_cycle.proposal.values import ActionProposal

PROPOSAL_RESULT_SCHEMA = "proposal_result"


@dataclass(frozen=True)
class ProposalResult:
    frame_id: str
    status: str
    candidates: tuple[ActionProposal, ...] = ()
    reason: str = ""
    schema: str = PROPOSAL_RESULT_SCHEMA

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "frame_id", require_ascii_id(self.frame_id, field_name="frame_id")
        )
        require_choice(self.schema, (PROPOSAL_RESULT_SCHEMA,), field_name="schema")
        require_choice(self.status, ("ok", "error"), field_name="status")
        if not isinstance(self.candidates, (list, tuple)):  # pyright: ignore[reportUnnecessaryIsInstance] - validate runtime callers
            raise FieldError("candidates", "must be an array")
        candidates = tuple(self.candidates)
        if not all(isinstance(item, ActionProposal) for item in candidates):
            raise FieldError("candidates", "must contain ActionProposal values")
        object.__setattr__(self, "candidates", candidates)
        if self.status == "ok":
            if self.reason != "":
                raise FieldError("reason", "must be empty when status is ok")
        elif self.status == "error":
            require_choice(self.reason, CYCLE_ERROR_REASONS, field_name="reason")
            if candidates:
                raise FieldError("candidates", "must be empty when status is error")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "frame_id": self.frame_id,
            "status": self.status,
            "reason": self.reason,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }

    @classmethod
    def from_dict(cls, payload: object) -> ProposalResult:
        document = require_schema(payload, PROPOSAL_RESULT_SCHEMA, {item.name for item in fields(cls)})
        candidates = document["candidates"]
        if not isinstance(candidates, (list, tuple)):
            raise FieldError("candidates", "must be an array")
        return cls(
            schema=document["schema"], frame_id=document["frame_id"],
            status=document["status"], reason=document["reason"],
            candidates=tuple(require_nested(ActionProposal.from_dict, item, field_name=f"candidates.{index}")
                             for index, item in enumerate(candidates)),
        )
