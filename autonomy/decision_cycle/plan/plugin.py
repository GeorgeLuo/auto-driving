"""Plan plugin protocol.

A plan plugin reconciles the cycle's proposal candidates into one
``ActionPlan``: the command the action step should act on, taken from one
candidate or composed from several, or none. The step runs exactly one
selected plan plugin.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from autonomy.decision_cycle.plan.values import ActionPlan
from autonomy.decision_cycle.proposal.inputs import DecisionDataSource
from autonomy.decision_cycle.proposal.values import ActionProposal
from autonomy.shared_memory import SharedMemory


@runtime_checkable
class PlanPlugin(Protocol):
    plugin_id: str

    def plan(
        self,
        candidates: tuple[ActionProposal, ...],
        source: DecisionDataSource | None,
        shared_memory: SharedMemory | None,
        *,
        frame_id: str,
        timestamp_ms: int,
    ) -> ActionPlan:
        """Return this frame's plan over ``candidates``; its ``selector_id`` is ``plugin_id``."""
