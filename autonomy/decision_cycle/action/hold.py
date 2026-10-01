"""Built-in hold action plugin.

``HoldAction`` records the selected command and always authorizes idle
control: the proposal is never applied. It is the action step's default
selection.
"""

from __future__ import annotations

from typing import Any

from autonomy.decision_cycle.action.values import ActionDecision
from autonomy.decision_cycle.plan.values import ActionPlan
from autonomy.runtime.control import AutonomyControl

PLUGIN_ID = "hold"
PLUGIN_SPEC = "autonomy.decision_cycle.action.hold:HoldAction"
HOLD_IDLE_REASON = "hold-idle"


def idle_output() -> dict[str, Any]:
    return {
        "steering": 0.0,
        "throttle": 0.0,
        "confidence": 1.0,
        "reason": HOLD_IDLE_REASON,
    }


def idle_control() -> AutonomyControl:
    return AutonomyControl(**idle_output())


class HoldAction:
    """Authorize idle control for every cycle."""

    plugin_id = PLUGIN_ID

    def decide(
        self,
        plan: ActionPlan | None,
        *,
        mode: str,
        error_reason: str | None = None,
    ) -> ActionDecision:
        return ActionDecision(control=idle_control(), applied=False)
