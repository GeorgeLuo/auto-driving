"""Propose straight-ahead movement regardless of perception or retained evidence."""

from __future__ import annotations

import math

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.observation.publication import read_observation
from autonomy.decision_cycle.proposal.values import (
    COMMAND_EPS, ActionProposal, ProposedVehicleCommand, SourceRef,
)


class DriveForward:
    plugin_id = "drive_forward"

    def __init__(self, *, throttle: float = 0.60) -> None:
        self.throttle = float(throttle)
        if not math.isfinite(self.throttle) or not COMMAND_EPS <= self.throttle <= 1:
            raise ValueError("throttle must be a finite normalized forward command")

    def propose(self, context: DecisionFrameContext) -> ActionProposal:
        return ActionProposal(
            plugin_id=self.plugin_id,
            frame_id=context.frame_id,
            lifecycle="fresh",
            freshness="fresh",
            confidence=1.0,
            reason="constant_forward_baseline",
            command=ProposedVehicleCommand(steering=0.0, throttle=self.throttle, gear="forward"),
            assumptions=("obstacles_ignored", "constant_normalized_throttle"),
            source_refs=(_source_ref(context),),
            available=True,
        )


def _source_ref(context: DecisionFrameContext) -> SourceRef:
    observation = read_observation(context.shared_memory)
    if observation is None:
        return SourceRef(kind="memory_record", id=context.frame_id, frame_id=context.frame_id, note="frame")
    return SourceRef(
        kind="observation", id=observation.observation_id, frame_id=context.frame_id,
        observation_id=observation.observation_id, note="current_observation",
    )
