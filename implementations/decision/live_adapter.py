"""Live PiCar adapter for the existing lateral-obstruction proposal."""

from __future__ import annotations

from typing import Any

from autonomy.runtime.engine import AutonomyControl, AutonomySnapshot

from .shadow_adapter import ShadowProposalsAutonomyEngine

ENGINE_ID = "obstacle-avoidance"
ADAPTER_ENGINE_SPEC = (
    "implementations.decision.live_adapter:ObstacleAvoidanceAutonomyEngine"
)
LIVE_MODES = frozenset({"autonomy", "local"})


class ObstacleAvoidanceAutonomyEngine:
    """Apply one selected lateral-obstruction proposal in explicit live mode.

    Proposal construction remains owned by the existing shadow-proposals engine.
    This adapter only turns its selected normalized command into the generic
    ``AutonomyControl`` consumed by Donkey's existing ``local`` drive mode.
    """

    def __init__(self, **engine_config: Any) -> None:
        self._proposal_engine = ShadowProposalsAutonomyEngine(**engine_config)

    def reset(self) -> None:
        self._proposal_engine.reset()

    def get_current_cycle_result(self) -> Any | None:
        """Expose the proposal cycle for the existing read-only viewer."""

        return self._proposal_engine.get_current_cycle_result()

    def describe_schema(self) -> dict[str, Any]:
        return {
            "schema": "autonomy_engine_schema_v0",
            "engine_id": ENGINE_ID,
            "engine_spec": ADAPTER_ENGINE_SPEC,
            "purpose": (
                "Happy-path lateral obstruction avoidance using the existing "
                "avoid_recent_obstruction proposal."
            ),
            "inputs": [
                "sensor_snapshot",
                "perception",
                "observation",
                "memory",
                "cycle",
                "mode",
                "user_steering",
                "user_throttle",
            ],
            "output": {
                "type": "AutonomyControl",
                "movement": (
                    "forward with opposite steering for fresh/recent lateral "
                    "obstruction evidence; otherwise idle"
                ),
                "live_modes": sorted(LIVE_MODES),
            },
            "stages": {
                "action": "obstacle_avoidance_proposal_run_cycle",
                "memory": "inspectable_snapshot",
            },
        }

    def step(self, snapshot: AutonomySnapshot) -> AutonomyControl:
        inner_control = self._proposal_engine.step(snapshot)
        cycle = self._proposal_engine.get_current_cycle_result()
        if cycle is None or getattr(cycle, "status", "error") != "ok":
            return AutonomyControl(
                confidence=1.0,
                reason="obstacle-avoidance-error",
                metadata={
                    "inner_reason": inner_control.reason,
                    "engine_id": ENGINE_ID,
                },
            )

        mode = snapshot.mode if isinstance(snapshot, AutonomySnapshot) else None
        if mode not in LIVE_MODES:
            return AutonomyControl(
                confidence=1.0,
                reason="autonomy-mode-required",
                metadata={"mode": mode, "engine_id": ENGINE_ID},
            )

        plan = getattr(cycle, "plan", None)
        selected = plan.selected_candidate() if plan is not None else None
        if selected is None or selected.command is None:
            return AutonomyControl(
                confidence=1.0,
                reason="no_lateral_obstruction",
                metadata={"engine_id": ENGINE_ID},
            )

        command = selected.command
        return AutonomyControl(
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
        )
