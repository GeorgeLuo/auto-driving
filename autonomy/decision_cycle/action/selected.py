"""Default action: authorize the selected plan; runtime owns movement mode."""
from __future__ import annotations

from typing import Any

from autonomy.decision_cycle.action.values import ActionDecision
from autonomy.decision_cycle.plan.values import ActionPlan
from autonomy.runtime.control import AutonomyControl


def _plan_metadata(plan: ActionPlan) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "proposal_ids": [item.proposal_id for item in plan.contributions],
    }
    if plan.status == "selected" and plan.selected_proposal_id is not None:
        metadata["proposal_id"] = plan.selected_proposal_id
    return metadata


class SelectedAction:
    plugin_id = "selected"

    def decide(self, plan: ActionPlan | None, *, mode: str,
               error_reason: str | None = None) -> ActionDecision:
        if plan is None or plan.command is None:
            return ActionDecision(AutonomyControl(reason=error_reason or "no-selected-command"))
        return ActionDecision(
            AutonomyControl(
                steering=plan.command.steering,
                throttle=plan.command.throttle,
                confidence=plan.confidence,
                reason=plan.reason,
                metadata=_plan_metadata(plan),
            ),
            applied=True,
        )
