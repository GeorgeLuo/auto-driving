from __future__ import annotations
from autonomy.decision.action_proposal import (
    ActionProposal,
    ProposedVehicleCommand,
    SourceRef,
)


def _active_proposal(
    *,
    plugin_id: str = "avoid_recent_obstruction",
    frame_id: str = "frame_001",
    confidence: float = 0.5,
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
        assumptions=("shadow_only",),
        source_refs=(
            SourceRef(kind="memory_record", id="r1", frame_id=frame_id, note="primary"),
        ),
        available=True,
    )
