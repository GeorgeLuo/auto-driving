"""Mode-gated action engine.

``ModeGate`` applies the selected proposal's command when the cycle succeeded
and the drive mode is in ``LIVE_MODES``; otherwise it returns idle control with
the reason. ``ModeGatedActionEngine`` runs the packaged obstruction proposal
through that gate, and the cycle result records whether the command was
applied.
"""

from __future__ import annotations

from typing import Any

from autonomy.decision_cycle.action_gate.values import GateDecision
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.planning.values import ActionPlan
from autonomy.decision_cycle.result import ActionResult
from autonomy.runtime.engine import AutonomyControl
from implementations.runtime.engines.catalog import create_action_composition

ENGINE_ID = "obstacle-avoidance"
GATE_ID = "mode"
ADAPTER_ENGINE_SPEC = "implementations.runtime.engines.mode_gated_action:ModeGatedActionEngine"
LIVE_MODES = frozenset({"autonomy", "local"})


class ModeGate:
    """Apply the selected command in a live drive mode; otherwise hold idle."""

    gate_id = GATE_ID

    def decide(
        self,
        plan: ActionPlan | None,
        *,
        mode: str,
        error_reason: str | None = None,
    ) -> GateDecision:
        if plan is None:
            return GateDecision(
                AutonomyControl(
                    confidence=1.0,
                    reason="obstacle-avoidance-error",
                    metadata={"error_reason": error_reason, "engine_id": ENGINE_ID},
                )
            )
        if mode not in LIVE_MODES:
            return GateDecision(
                AutonomyControl(
                    confidence=1.0,
                    reason="autonomy-mode-required",
                    metadata={"mode": mode, "engine_id": ENGINE_ID},
                )
            )
        selected = plan.selected_candidate()
        if selected is None or selected.command is None:
            return GateDecision(
                AutonomyControl(
                    confidence=1.0,
                    reason="no_lateral_obstruction",
                    metadata={"engine_id": ENGINE_ID},
                )
            )
        command = selected.command
        return GateDecision(
            AutonomyControl(
                steering=command.steering,
                throttle=command.throttle,
                confidence=selected.confidence,
                reason=selected.reason,
                metadata={
                    "engine_id": ENGINE_ID,
                    "proposal_id": selected.proposal_id,
                    "proposal_lifecycle": selected.lifecycle,
                    "proposal_freshness": selected.freshness,
                },
            ),
            applied=True,
        )


class ModeGatedActionEngine:
    """Apply the selected obstruction proposal when the drive mode permits it."""

    def __init__(self, **engine_config: Any) -> None:
        self.composition = create_action_composition(engine_config or None, gate=ModeGate())

    def reset(self) -> None:
        return None

    def describe_schema(self) -> dict[str, Any]:
        return {
            "schema": "autonomy_engine_schema_v0",
            "engine_id": ENGINE_ID,
            "engine_spec": ADAPTER_ENGINE_SPEC,
            "purpose": (
                "Happy-path lateral obstruction avoidance using the existing "
                "avoid_recent_obstruction proposal."
            ),
            "inputs": ["context", "perception", "observation", "memory"],
            "output": {
                "type": "ActionResult",
                "movement": (
                    "forward with opposite steering for fresh/recent lateral "
                    "obstruction evidence; otherwise idle"
                ),
                "gate": GATE_ID,
                "live_modes": sorted(LIVE_MODES),
            },
            "steps": {
                "action": "propose_plan_mode_gate",
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
