"""Hold action gate.

The hold gate records the selected command and always authorizes idle
control: the proposal is never applied.
"""

from __future__ import annotations

from typing import Any

from autonomy.decision_cycle.action_gate.values import GateDecision
from autonomy.decision_cycle.planning.values import ActionPlan
from autonomy.runtime.engine import AutonomyControl

GATE_ID = "hold"
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


class HoldGate:
    """Authorize idle control for every cycle."""

    gate_id = GATE_ID

    def decide(
        self,
        plan: ActionPlan | None,
        *,
        mode: str,
        error_reason: str | None = None,
    ) -> GateDecision:
        return GateDecision(control=idle_control(), applied=False)
