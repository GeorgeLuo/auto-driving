"""Action plugin protocol.

An action plugin decides the control the cycle applies. It sees the plan (or,
when there is none, the reason the cycle could not plan) and the drive mode.
Gating is what an action plugin does: hold always authorizes idle control, a
mode-gated action applies the selected command only in live modes. The step
runs exactly one selected action plugin.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from autonomy.decision_cycle.action.values import ActionDecision
from autonomy.decision_cycle.plan.values import ActionPlan


@runtime_checkable
class ActionPlugin(Protocol):
    plugin_id: str

    def decide(
        self,
        plan: ActionPlan | None,
        *,
        mode: str,
        error_reason: str | None = None,
    ) -> ActionDecision:
        """Return the control for this cycle. ``plan`` is None exactly when ``error_reason`` is set."""
