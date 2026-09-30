"""Mode-gated action engine.

``ObstacleAvoidanceAutonomyEngine`` runs the hold action engine to obtain the
cycle result, then converts the selected proposal into control only when the
cycle succeeded and the drive mode is in ``LIVE_MODES``. Otherwise it returns
idle control with the reason. It is bound to the existing obstruction proposal
and mode policy; ``ModeGatedActionAutonomyEngine`` names the same class.

``ADAPTER_ENGINE_SPEC`` keeps the spec string existing activations use.
"""

from __future__ import annotations

from typing import Any

from autonomy.runtime.engine import AutonomyControl, AutonomySnapshot

from implementations.runtime.engines.hold_action import ShadowProposalsAutonomyEngine

ENGINE_ID = "obstacle-avoidance"
ADAPTER_ENGINE_SPEC = (
    "implementations.runtime.engines.mode_gated_action:ObstacleAvoidanceAutonomyEngine"
)
LIVE_MODES = frozenset({"autonomy", "local"})


class ObstacleAvoidanceAutonomyEngine:
    """Apply the selected obstruction proposal when the drive mode permits it.

    The inner hold action engine builds proposals and returns idle control.
    This engine reads that engine's cycle result and turns its selected
    command into the ``AutonomyControl`` consumed by Donkey's ``local`` drive
    mode.
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
            "steps": {
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


ModeGatedActionAutonomyEngine = ObstacleAvoidanceAutonomyEngine
