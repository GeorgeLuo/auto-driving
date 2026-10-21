"""Propose straight-back movement regardless of perception or retained evidence."""

from __future__ import annotations

import math

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.observation.publication import read_observation
from autonomy.decision_cycle.proposal.values import (
    COMMAND_EPS, ActionProposal, ProposedVehicleCommand, SourceRef,
)


class DriveBackwards:
    plugin_id = "drive_backwards"

    def __init__(self, *, throttle: float = -0.60) -> None:
        self.throttle = float(throttle)
        if not math.isfinite(self.throttle) or not -1 <= self.throttle <= -COMMAND_EPS:
            raise ValueError("throttle must be a finite normalized reverse command")

    def propose(self, context: DecisionFrameContext) -> ActionProposal:
        observation = read_observation(context.shared_memory)
        if observation is None:
            return ActionProposal(
                plugin_id=self.plugin_id, frame_id=context.frame_id,
                lifecycle="missing_input", freshness="none", confidence=None,
                reason="no_observation", command=None, source_refs=(), available=False,
            )
        return ActionProposal(
            plugin_id=self.plugin_id,
            frame_id=context.frame_id,
            lifecycle="fresh",
            freshness="fresh",
            confidence=1.0,
            reason="constant_reverse_baseline",
            command=ProposedVehicleCommand(steering=0.0, throttle=self.throttle, gear="reverse"),
            assumptions=("obstacles_ignored", "constant_normalized_throttle"),
            source_refs=(SourceRef(
                kind="observation", id=observation.observation_id, frame_id=context.frame_id,
                observation_id=observation.observation_id, note="current_observation",
            ),),
            available=True,
        )
