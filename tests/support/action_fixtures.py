"""Action compositions whose gate returns a fixed control."""

from __future__ import annotations

from autonomy.decision_cycle.action import ActionComposition
from autonomy.decision_cycle.action_gate.values import GateDecision
from autonomy.runtime.engine import AutonomyControl


class FixedGate:
    """Gate that authorizes ``control`` for every cycle without applying a proposal."""

    def __init__(self, control: AutonomyControl, gate_id: str = "test") -> None:
        self.control = control
        self.gate_id = gate_id

    def decide(self, plan, *, mode, error_reason=None) -> GateDecision:
        return GateDecision(self.control)


def fixed_control_composition(
    control: AutonomyControl,
    *,
    gate_id: str = "test",
) -> ActionComposition:
    """One plugin that returns nothing, so the plan is idle and the gate decides."""

    return ActionComposition(
        plugins={"noop": lambda source, shared_memory: None},
        gate=FixedGate(control, gate_id),
    )
