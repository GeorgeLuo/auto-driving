"""Built-in plan plugin: select the most confident active candidate.

A candidate is active when its lifecycle is fresh or retained and matches its
freshness, it is available, it carries a command, and its proposal ID names
this frame. The active candidate with the highest confidence is selected; equal
confidence goes to the lower plugin ID. With no active candidate the plan is
idle. ``HighestConfidencePlan`` is the plan step's default selection and
records its plugin ID as the plan's ``selector_id``.
"""

from __future__ import annotations

from typing import Any

from autonomy.decision_cycle.plan.values import SELECTOR_ID, ActionPlan, PlanContribution
from autonomy.decision_cycle.proposal.values import ActionProposal


def select_highest_confidence_plan(
    *,
    frame_id: str,
    timestamp_ms: int,
    candidates: list[ActionProposal] | tuple[ActionProposal, ...],
    metadata: dict[str, Any] | None = None,
) -> ActionPlan:
    candidates_list = list(candidates)
    active = [
        item
        for item in candidates_list
        if item.lifecycle in {"fresh", "retained"}
        and item.freshness == item.lifecycle
        and item.available
        and item.command is not None
        and item.proposal_id.endswith(f":{frame_id}")
    ]
    plan_metadata: dict[str, Any] = {} if metadata is None else metadata
    if not active:
        return ActionPlan(
            frame_id=frame_id,
            timestamp_ms=timestamp_ms,
            status="idle",
            candidates=tuple(candidates_list),
            selected_proposal_id=None,
            contributions=(),
            metadata=plan_metadata,
        )
    active.sort(key=lambda item: (-item.confidence, item.plugin_id))
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
        metadata=plan_metadata,
    )


PLUGIN_ID = SELECTOR_ID
PLUGIN_SPEC = "autonomy.decision_cycle.plan.highest_confidence:HighestConfidencePlan"


class HighestConfidencePlan:
    """Plan plugin wrapping ``select_highest_confidence_plan``."""

    plugin_id = PLUGIN_ID

    def plan(
        self,
        candidates: tuple[ActionProposal, ...],
        *,
        frame_id: str,
        timestamp_ms: int,
    ) -> ActionPlan:
        return select_highest_confidence_plan(
            frame_id=frame_id, timestamp_ms=timestamp_ms, candidates=candidates
        )
