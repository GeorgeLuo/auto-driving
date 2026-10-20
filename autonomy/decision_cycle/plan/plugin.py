"""Plan plugin protocol.

A plan plugin reconciles the cycle's proposal candidates into one
``ActionPlan``: the command the action step should act on, taken from one
candidate or composed from several, or none. The step runs exactly one
selected plan plugin.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from autonomy.decision_cycle.plan.values import ActionPlan
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.proposal.values import ActionProposal


@runtime_checkable
class PlanPlugin(Protocol):
    plugin_id: str

    def plan(
        self,
        context: DecisionFrameContext,
        candidates: tuple[ActionProposal, ...],
    ) -> ActionPlan:
        """Return this frame's plan over ``candidates``; its ``selector_id`` is ``plugin_id``."""
