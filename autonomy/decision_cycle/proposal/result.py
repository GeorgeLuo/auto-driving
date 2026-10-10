"""The proposal step's record for one cycle.

``ProposalResult`` holds the detached source the plugins read and one admitted
candidate per selected plugin. Status ``error`` records why the step could not
produce candidates; the plan step then does not plan and the action step
fails closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from autonomy.decision_cycle.action_identifiers import require_ascii_id
from autonomy.decision_cycle.errors import CYCLE_ERROR_REASONS
from autonomy.decision_cycle.proposal.inputs import DecisionDataSource
from autonomy.decision_cycle.proposal.values import ActionProposal

PROPOSAL_RESULT_SCHEMA = "proposal_result_v0"


@dataclass(frozen=True)
class ProposalResult:
    frame_id: str
    status: str
    candidates: tuple[ActionProposal, ...] = ()
    reason: str = ""
    source: DecisionDataSource | None = None
    schema: str = PROPOSAL_RESULT_SCHEMA

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "frame_id", require_ascii_id(self.frame_id, field_name="frame_id")
        )
        if self.schema != PROPOSAL_RESULT_SCHEMA:
            raise ValueError(f"schema must be {PROPOSAL_RESULT_SCHEMA!r}; got {self.schema!r}")
        candidates = tuple(self.candidates)
        if not all(isinstance(item, ActionProposal) for item in candidates):
            raise TypeError("candidates must be ActionProposal")
        object.__setattr__(self, "candidates", candidates)
        if self.source is not None and not isinstance(self.source, DecisionDataSource):
            raise TypeError("source must be DecisionDataSource or None")
        if self.status == "ok":
            if self.reason != "":
                raise ValueError("ok reason must be empty")
        elif self.status == "error":
            if self.reason not in CYCLE_ERROR_REASONS:
                raise ValueError(f"unknown proposal error reason {self.reason!r}")
            if candidates:
                raise ValueError("error requires no candidates")
        else:
            raise ValueError(f"invalid proposal status {self.status!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "frame_id": self.frame_id,
            "status": self.status,
            "reason": self.reason,
            "source": self.source.to_dict() if self.source is not None else None,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }
