"""Default action: authorize the selected plan; runtime owns movement mode."""
from __future__ import annotations

from autonomy.decision_cycle.action.values import ActionDecision
from autonomy.decision_cycle.plan.values import ActionPlan
from autonomy.runtime.control import AutonomyControl


class SelectedAction:
    plugin_id = "selected"

    def decide(self, plan: ActionPlan | None, *, mode: str,
               error_reason: str | None = None) -> ActionDecision:
        selected = plan.selected_candidate() if plan is not None else None
        if selected is None or selected.command is None:
            return ActionDecision(AutonomyControl(reason=error_reason or "no-selected-command"))
        return ActionDecision(
            AutonomyControl(
                steering=selected.command.steering,
                throttle=selected.command.throttle,
                confidence=selected.confidence,
                reason=selected.reason,
                metadata={"proposal_id": selected.proposal_id},
            ),
            applied=True,
        )
