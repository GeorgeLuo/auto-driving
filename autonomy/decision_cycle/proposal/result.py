"""The proposal step's record for one cycle.

``ProposalResult`` holds one admitted
candidate per selected plugin. Status ``error`` records why the step could not
produce candidates; the plan step then does not plan and the action step
fails closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from autonomy.decision_cycle.action_identifiers import require_ascii_id
from autonomy.decision_cycle.errors import CYCLE_ERROR_REASONS
from autonomy.decision_cycle.proposal.values import ActionProposal
from autonomy.serialization import require_canonical_export, require_exact_keys

PROPOSAL_RESULT_SCHEMA = "proposal_result"
PROPOSAL_RECORD_EXACT_KEYS = frozenset(
    {"schema", "frame_id", "status", "reason", "candidates"}
)


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
        if self.schema != PROPOSAL_RESULT_SCHEMA:
            raise ValueError(f"schema must be {PROPOSAL_RESULT_SCHEMA!r}; got {self.schema!r}")
        candidates = tuple(self.candidates)
        if not all(isinstance(item, ActionProposal) for item in candidates):
            raise TypeError("candidates must be ActionProposal")
        object.__setattr__(self, "candidates", candidates)
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
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }

    @classmethod
    def decode(cls, payload: object, *, field: str = "cycle.proposal") -> "ProposalResult":
        """The proposal step, when ``payload`` is exactly its export."""

        if not isinstance(payload, dict):
            raise ValueError(f"{field} must be an object.")
        if payload.get("schema") != PROPOSAL_RESULT_SCHEMA:
            raise ValueError(f"{field}.schema must be {PROPOSAL_RESULT_SCHEMA!r}.")
        require_exact_keys(payload, PROPOSAL_RECORD_EXACT_KEYS, field=field)
        if payload.get("status") not in {"ok", "error"}:
            raise ValueError(f"{field}.status must be ok or error.")
        if type(payload.get("reason")) is not str:
            raise ValueError(f"{field}.reason must be a string.")
        raw_candidates = payload.get("candidates")
        if not isinstance(raw_candidates, list):
            raise ValueError(f"{field}.candidates must be a list.")
        candidates = tuple(
            ActionProposal.decode(item, field=f"{field}.candidates[{index}]")
            for index, item in enumerate(raw_candidates)
        )
        try:
            proposal = cls(
                frame_id=str(payload["frame_id"]),
                status=str(payload["status"]),
                reason=str(payload["reason"]),
                candidates=candidates,
                schema=str(payload["schema"]),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field} failed ProposalResult construction: {exc}") from exc
        require_canonical_export(payload, proposal.to_dict(), field=field)
        return proposal
