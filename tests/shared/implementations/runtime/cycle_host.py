"""A host that fails before producing a cycle result."""
from __future__ import annotations

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.runtime.cycle_host import AutonomyCycleHost
from implementations.runtime.picar import DonkeyControlTarget


class ExplodingHost(AutonomyCycleHost):
    def __init__(self) -> None:
        super().__init__(target=DonkeyControlTarget())

    def run(self, context: DecisionFrameContext, **inputs):
        raise RuntimeError("cycle failed")
