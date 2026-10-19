"""Select only a configured proposal plugin; idle if its candidate is unavailable."""

from __future__ import annotations

from autonomy.decision_cycle.action_identifiers import require_ascii_id
from autonomy.decision_cycle.plan.values import ActionPlan, PlanContribution
from autonomy.decision_cycle.proposal.inputs import DecisionDataSource
from autonomy.decision_cycle.proposal.values import ActionProposal
from autonomy.shared_memory import SharedMemory


class SelectProposal:
    plugin_id = "select_proposal"

    def __init__(self, *, proposal_plugin_id: str = "drive_forward") -> None:
        self.proposal_plugin_id = require_ascii_id(proposal_plugin_id, field_name="proposal_plugin_id")

    def plan(
        self,
        candidates: tuple[ActionProposal, ...],
        source: DecisionDataSource | None,
        shared_memory: SharedMemory | None,
        *,
        frame_id: str,
        timestamp_ms: int,
    ) -> ActionPlan:
        selected = next((candidate for candidate in candidates
                         if candidate.plugin_id == self.proposal_plugin_id
                         and candidate.is_active(frame_id)), None)
        if selected is None:
            return ActionPlan(
                frame_id=frame_id, timestamp_ms=timestamp_ms, status="idle",
                candidates=candidates, selector_id=self.plugin_id,
            )
        return ActionPlan(
            frame_id=frame_id,
            timestamp_ms=timestamp_ms,
            status="selected",
            candidates=candidates,
            selector_id=self.plugin_id,
            selected_proposal_id=selected.proposal_id,
            contributions=(PlanContribution(
                proposal_id=selected.proposal_id, plugin_id=selected.plugin_id,
            ),),
            command=selected.command,
            reason=selected.reason,
            confidence=selected.confidence,
        )
