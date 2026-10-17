"""Plan plugin: select the most confident active candidate.

Of the candidates ``ActionProposal.is_active`` admits for this frame, the one
with the highest confidence is selected. A missing confidence ranks below
every number. Equal confidence goes to the lower plugin ID. With no active
candidate the plan is idle. ``HighestConfidencePlan`` records its plugin ID
as the plan's ``selector_id``.
"""

from __future__ import annotations

from typing import Any

from autonomy.decision_cycle.confidence import confidence_rank
from autonomy.decision_cycle.plan.values import ActionPlan, PlanContribution
from autonomy.decision_cycle.proposal.inputs import DecisionDataSource
from autonomy.decision_cycle.proposal.values import ActionProposal
from autonomy.shared_memory import SharedMemory


def select_highest_confidence_plan(
    *,
    frame_id: str,
    timestamp_ms: int,
    candidates: list[ActionProposal] | tuple[ActionProposal, ...],
    selector_id: str,
    metadata: dict[str, Any] | None = None,
) -> ActionPlan:
    candidates_list = list(candidates)
    active = [item for item in candidates_list if item.is_active(frame_id)]
    plan_metadata: dict[str, Any] = {} if metadata is None else metadata
    if not active:
        return ActionPlan(
            frame_id=frame_id,
            timestamp_ms=timestamp_ms,
            status="idle",
            candidates=tuple(candidates_list),
            selected_proposal_id=None,
            contributions=(),
            command=None,
            reason="",
            confidence=None,
            selector_id=selector_id,
            metadata=plan_metadata,
        )
    active.sort(key=lambda item: (*confidence_rank(item.confidence), item.plugin_id))
    selected = active[0]
    return ActionPlan(
        frame_id=frame_id,
        timestamp_ms=timestamp_ms,
        status="selected",
        candidates=tuple(candidates_list),
        selected_proposal_id=selected.proposal_id,
        contributions=(
            PlanContribution(
                proposal_id=selected.proposal_id,
                plugin_id=selected.plugin_id,
                weight=1.0,
                role="selected",
            ),
        ),
        command=selected.command,
        reason=selected.reason,
        confidence=selected.confidence,
        selector_id=selector_id,
        metadata=plan_metadata,
    )


class HighestConfidencePlan:
    """Plan plugin wrapping ``select_highest_confidence_plan``."""

    plugin_id = "highest_confidence"

    def plan(
        self,
        candidates: tuple[ActionProposal, ...],
        source: DecisionDataSource | None,
        shared_memory: SharedMemory | None,
        *,
        frame_id: str,
        timestamp_ms: int,
    ) -> ActionPlan:
        return select_highest_confidence_plan(
            frame_id=frame_id,
            timestamp_ms=timestamp_ms,
            candidates=candidates,
            selector_id=self.plugin_id,
        )
