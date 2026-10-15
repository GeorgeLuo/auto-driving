from __future__ import annotations
from autonomy.decision_cycle.plan.values import ActionPlan, PlanContribution
from autonomy.decision_cycle.proposal.values import (
    ActionProposal,
    ProposedVehicleCommand,
    SourceRef,
)


def _active_proposal(
    *,
    plugin_id: str = "avoid_recent_obstruction",
    frame_id: str = "frame_001",
    confidence: float | None = 0.5,
    steering: float = 0.35,
) -> ActionProposal:
    return ActionProposal(
        plugin_id=plugin_id,
        frame_id=frame_id,
        lifecycle="fresh",
        freshness="fresh",
        confidence=confidence,
        reason="test",
        command=ProposedVehicleCommand(steering=steering, throttle=0.0, gear="hold"),
        assumptions=("image_relative_only",),
        source_refs=(
            SourceRef(kind="memory_record", id="r1", frame_id=frame_id, note="primary"),
        ),
        available=True,
    )


class WeightedMeanPlan:
    """A composing plan plugin: the weighted mean of the active candidates' commands."""

    plugin_id = "weighted_mean"

    def __init__(self, weights: dict[str, float]) -> None:
        self.weights = weights

    def plan(self, candidates, source, shared_memory, *, frame_id, timestamp_ms):
        del source, shared_memory
        active = [item for item in candidates if item.is_active(frame_id)]
        total = sum(self.weights[item.plugin_id] for item in active)
        steering = sum(self.weights[item.plugin_id] * item.command.steering for item in active) / total
        throttle = sum(self.weights[item.plugin_id] * item.command.throttle for item in active) / total
        return ActionPlan(
            frame_id=frame_id,
            timestamp_ms=timestamp_ms,
            status="composed",
            candidates=tuple(candidates),
            selector_id=self.plugin_id,
            selected_proposal_id=None,
            contributions=tuple(
                PlanContribution(
                    proposal_id=item.proposal_id,
                    plugin_id=item.plugin_id,
                    weight=self.weights[item.plugin_id],
                    role="mix",
                )
                for item in active
            ),
            command=ProposedVehicleCommand(steering=steering, throttle=throttle, gear="hold"),
            reason="weighted_mean",
            confidence=None,
        )
