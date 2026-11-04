"""Built-in hold action plugin.

``HoldAction`` records the selected command and always authorizes idle
control: the proposal is never applied. It is an explicitly selected
selection.
"""

from __future__ import annotations

from typing import Any

from autonomy.decision_cycle.action.values import ActionDecision, AuthorityResult
from autonomy.decision_cycle.plan.values import ActionPlan
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.runtime.control import AutonomyControl
from autonomy.serialization import require_canonical_export

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

    plugin_id = "hold"

    @classmethod
    def validate_authority(cls, authority: AuthorityResult, *, field: str) -> None:
        """A published hold must authorize idle without applying the proposal."""
        if authority.gate_id != cls.plugin_id:
            return
        if authority.proposed_applied:
            raise ValueError(f"{field}.proposed_applied must be false for the hold gate.")
        require_canonical_export(
            authority.authorized_output, idle_output(), field=f"{field}.authorized_output",
        )

    def decide(
        self,
        context: DecisionFrameContext,
        plan: ActionPlan | None,
        *,
        error_reason: str | None = None,
    ) -> ActionDecision:
        return ActionDecision(control=idle_control(), applied=False)
