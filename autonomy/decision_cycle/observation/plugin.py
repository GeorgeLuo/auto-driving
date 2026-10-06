"""Observation plugin protocol.

An observation plugin turns the cycle's perception evidence and sensor context
into the current-frame ``Observation`` that memory and proposal read. The step
runs exactly one selected observation plugin.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.perception.interface import PerceptionText


@runtime_checkable
class ObservationPlugin(Protocol):
    plugin_id: str

    def observe(
        self, context: DecisionFrameContext, perception: PerceptionText | None
    ) -> Observation | None:
        """Return this frame's observation, or ``None`` when there is none."""
