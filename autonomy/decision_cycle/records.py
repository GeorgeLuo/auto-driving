"""Proposal, plan, and action records shared by cycles and report readers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from autonomy.decision_cycle.action.result import ActionResult
from autonomy.decision_cycle.action.values import AuthorityResult
from autonomy.decision_cycle.plan.values import ActionPlan
from autonomy.decision_cycle.proposal.result import ProposalResult
from autonomy.runtime.control import AutonomyControl


@dataclass(frozen=True)
class DecisionRecords:
    """Proposal, plan, and action for one frame."""

    proposal: ProposalResult
    plan: ActionPlan | None
    action: ActionResult

    @property
    def frame_id(self) -> str:
        return self.action.frame_id

    @property
    def status(self) -> str:
        return self.action.status

    @property
    def reason(self) -> str:
        return self.action.reason

    @property
    def authority(self) -> AuthorityResult:
        return self.action.authority

    @property
    def control(self) -> AutonomyControl | None:
        return self.action.control

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal": self.proposal.to_dict(),
            "plan": self.plan.to_dict() if self.plan is not None else None,
            "action": self.action.to_dict(),
        }

    @classmethod
    def from_cycle(cls, cycle: Any) -> DecisionRecords | None:
        """The decision records of a cycle result, when it has them."""

        proposal = getattr(cycle, "proposal", None)
        action = getattr(cycle, "action", None)
        if not isinstance(proposal, ProposalResult) or not isinstance(action, ActionResult):
            return None
        return cls(proposal=proposal, plan=getattr(cycle, "plan", None), action=action)
