"""Propose straight-back movement regardless of perception or retained evidence."""

from __future__ import annotations

import math

from autonomy.decision_cycle.proposal.inputs import DecisionDataSource
from autonomy.decision_cycle.proposal.values import (
    COMMAND_EPS, ActionProposal, ProposedVehicleCommand, SourceRef,
)
from autonomy.shared_memory import SharedMemory


class DriveBackwards:
    plugin_id = "drive_backwards"

    def __init__(self, *, throttle: float = -0.60) -> None:
        self.throttle = float(throttle)
        if not math.isfinite(self.throttle) or not -1 <= self.throttle <= -COMMAND_EPS:
            raise ValueError("throttle must be a finite normalized reverse command")

    def propose(self, source: DecisionDataSource, shared_memory: SharedMemory | None) -> ActionProposal:
        return ActionProposal(
            plugin_id=self.plugin_id,
            frame_id=source.frame_id,
            lifecycle="fresh",
            freshness="fresh",
            confidence=1.0,
            reason="constant_reverse_baseline",
            command=ProposedVehicleCommand(steering=0.0, throttle=self.throttle, gear="reverse"),
            assumptions=("obstacles_ignored", "constant_normalized_throttle"),
            source_refs=(SourceRef(
                kind="capability", id="max_abs_throttle", frame_id=source.frame_id,
                note="normalized_throttle_command",
            ),),
            available=True,
        )
