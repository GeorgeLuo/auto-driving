"""Mode-gated action plugin.

``ModeAction`` applies the plan's selected command when the cycle planned and
the drive mode is in ``LIVE_MODES``; otherwise it authorizes idle control with
the reason. The action record describes authorization; shared execution reports delivery.
"""

from __future__ import annotations

from typing import Any

from autonomy.decision_cycle.action.values import ActionDecision
from autonomy.decision_cycle.plan.values import ActionPlan
from autonomy.runtime.control import AutonomyControl

LIVE_MODES = frozenset({"autonomy", "local"})


class ModeAction:
    """Apply the selected command in a live drive mode; otherwise hold idle."""

    plugin_id = "mode"

    def decide(
        self,
        plan: ActionPlan | None,
        *,
        mode: str,
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
        metadata: dict[str, Any] = {
            "plugin_id": self.plugin_id,
            "proposal_ids": [item.proposal_id for item in plan.contributions],
        }
        if plan.status == "selected":
            selected = plan.selected_candidate()
            metadata["proposal_id"] = plan.selected_proposal_id
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
