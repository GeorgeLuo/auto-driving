"""Mode-gated action plugin.

``ModeAction`` applies the plan's command when the cycle planned and the
drive mode is in ``LIVE_MODES``; otherwise it authorizes idle control with
the reason. The action record describes authorization; shared execution reports delivery.
"""

from __future__ import annotations

from typing import Any

from autonomy.decision_cycle.action.publication import read_mode
from autonomy.decision_cycle.action.values import ActionDecision, plan_control_metadata
from autonomy.decision_cycle.plan.values import ActionPlan
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.runtime.control import AutonomyControl

LIVE_MODES = frozenset({"autonomy", "local"})


class ModeAction:
    """Apply the plan's command in a live drive mode; otherwise hold idle."""

    plugin_id = "mode"

    def decide(
        self,
        context: DecisionFrameContext,
        plan: ActionPlan | None,
        *,
        error_reason: str | None = None,
    ) -> ActionDecision:
        if plan is None:
            return ActionDecision(
                AutonomyControl(
                    confidence=1.0,
                    reason="mode-action-error",
                    metadata={"error_reason": error_reason, "plugin_id": self.plugin_id},
                )
            )
        mode = read_mode(context.shared_memory)
        if mode not in LIVE_MODES:
            return ActionDecision(
                AutonomyControl(
                    confidence=1.0,
                    reason="autonomy-mode-required",
                    metadata={"mode": mode, "plugin_id": self.plugin_id},
                )
            )
        if plan.command is None:
            return ActionDecision(
                AutonomyControl(
                    confidence=1.0,
                    reason="no_selected_command",
                    metadata={"plugin_id": self.plugin_id},
                )
            )
        command = plan.command
        metadata: dict[str, Any] = {"plugin_id": self.plugin_id, **plan_control_metadata(plan)}
        selected = plan.selected_candidate()
        if selected is not None:
            metadata["proposal_lifecycle"] = selected.lifecycle
            metadata["proposal_freshness"] = selected.freshness
        return ActionDecision(
            AutonomyControl(
                steering=command.steering,
                throttle=command.throttle,
                confidence=plan.confidence,
                reason=plan.reason,
                metadata=metadata,
            ),
            applied=True,
        )
