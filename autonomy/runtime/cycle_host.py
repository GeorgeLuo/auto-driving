from __future__ import annotations

from dataclasses import replace
from typing import Any

from autonomy.decision.cycle import (
    DecisionCycle,
    DecisionCycleResult,
    DecisionFrameContext,
    DecisionStages,
)
from .engine import AutonomySnapshot
from .manager import AutonomyManager


class AutonomyCycleHost:
    """Run one strict staged cycle around a loadable autonomy engine."""

    def __init__(
        self,
        *,
        manager: AutonomyManager | None = None,
        stages: DecisionStages | None = None,
    ) -> None:
        configured_stages = stages or DecisionStages()
        if configured_stages.choose_action is not None:
            raise ValueError("AutonomyCycleHost owns the decision action stage")

        self.manager = manager or AutonomyManager()
        self.cycle = DecisionCycle(
            replace(configured_stages, choose_action=self._choose_action),
        )
        self.last_result: DecisionCycleResult | None = None

    def run(self, context: DecisionFrameContext) -> DecisionCycleResult:
        result = self.cycle.run(context)
        self.last_result = result
        return result

    def status(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "engine": self.manager.status(),
            "last_cycle": self.last_result.to_dict() if self.last_result is not None else None,
        }
        remember = self.cycle.stages.remember
        if remember is not None and callable(getattr(remember, "status", None)):
            payload["memory"] = remember.status()
        return payload

    def _choose_action(
        self,
        context,
        perception,
        observation,
        memory,
        patterns,
        projections,
    ):
        del patterns, projections
        return self.manager.step(
            AutonomySnapshot(
                sensor_snapshot=context.sensor_snapshot,
                perception=perception,
                observation=observation,
                memory=memory,
                cycle={
                    "frame_id": context.frame_id,
                    "frame_index": context.frame_index,
                },
                mode=context.mode,
                user_steering=context.user_steering,
                user_throttle=context.user_throttle,
                timestamp_ms=context.timestamp_ms,
                metadata=dict(context.metadata),
            )
        )
