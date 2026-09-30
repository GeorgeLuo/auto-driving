"""Hold action engine.

``HoldActionEngine`` runs the packaged proposals through the hold gate for
each cycle: proposals may be nonzero while the authorized control stays idle.
The cycle result records the selected command and ``proposed_applied=false``.
"""

from __future__ import annotations

from typing import Any

from autonomy.decision_cycle.action_gate.hold import GATE_ID, HOLD_IDLE_REASON, HoldGate
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.result import ActionResult
from implementations.runtime.engines.catalog import create_action_composition

ENGINE_ID = "hold-action"
ADAPTER_ENGINE_SPEC = "implementations.runtime.engines.hold_action:HoldActionEngine"


class HoldActionEngine:
    """AutonomyManager engine that proposes and plans, then holds idle."""

    def __init__(self, **engine_config: Any) -> None:
        # The catalog owns the proposal document. Missing keys use its named
        # defaults; unknown keys and invalid values fail closed.
        self.composition = create_action_composition(engine_config or None, gate=HoldGate())

    def reset(self) -> None:
        return None

    def describe_schema(self) -> dict[str, Any]:
        return {
            "schema": "autonomy_engine_schema_v0",
            "engine_id": ENGINE_ID,
            "engine_spec": ADAPTER_ENGINE_SPEC,
            "purpose": (
                "Proposals may be nonzero while the authorized AutonomyControl "
                f"remains idle ({HOLD_IDLE_REASON})."
            ),
            "inputs": ["context", "perception", "observation", "memory"],
            "output": {
                "type": "ActionResult",
                "movement": "always idle",
                "gate": GATE_ID,
            },
            "steps": {
                "action": "propose_plan_hold",
                "memory": "inspectable_snapshot",
            },
        }

    def act(
        self,
        context: DecisionFrameContext,
        perception: Any,
        observation: Any,
        memory: Any,
    ) -> ActionResult:
        return self.composition.act(context, perception, observation, memory)
