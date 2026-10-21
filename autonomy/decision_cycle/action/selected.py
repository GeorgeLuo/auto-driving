"""Default action: apply the plan's command; runtime owns movement mode."""
from __future__ import annotations

from autonomy.decision_cycle.action.values import ActionDecision, plan_control_metadata
from autonomy.decision_cycle.plan.values import ActionPlan
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.runtime.control import AutonomyControl


class SelectedAction:
    plugin_id = "selected"

    def decide(self, context: DecisionFrameContext, plan: ActionPlan | None, *,
               error_reason: str | None = None) -> ActionDecision:
        if plan is None or plan.command is None:
            return ActionDecision(AutonomyControl(reason=error_reason or "no-selected-command"))
        return ActionDecision(
            AutonomyControl(
                steering=plan.command.steering,
                throttle=plan.command.throttle,
                confidence=plan.confidence,
                reason=plan.reason,
                metadata=plan_control_metadata(plan),
            ),
            applied=True,
        )
