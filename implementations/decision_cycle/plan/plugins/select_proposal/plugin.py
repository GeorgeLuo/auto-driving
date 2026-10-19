"""Select only a configured proposal plugin; idle if its candidate is unavailable.

With ``view_static_proposal_plugin_id`` set, the current observation's
``view_static`` signal switches the target: while it is ``True`` the plan
selects that plugin instead. With ``override_proposal_plugin_id`` set, that
plugin's candidate wins whenever it is active this frame.
"""

from __future__ import annotations

from autonomy.decision_cycle.action_identifiers import require_ascii_id
from autonomy.decision_cycle.plan.values import ActionPlan, PlanContribution
from autonomy.decision_cycle.proposal.inputs import DecisionDataSource
from autonomy.decision_cycle.proposal.values import ActionProposal
from autonomy.shared_memory import SharedMemory


class SelectProposal:
    plugin_id = "select_proposal"

    def __init__(
        self,
        *,
        proposal_plugin_id: str = "drive_forward",
        view_static_proposal_plugin_id: str | None = None,
        override_proposal_plugin_id: str | None = None,
    ) -> None:
        self.proposal_plugin_id = require_ascii_id(proposal_plugin_id, field_name="proposal_plugin_id")
        self.view_static_proposal_plugin_id = None if view_static_proposal_plugin_id is None else (
            require_ascii_id(view_static_proposal_plugin_id, field_name="view_static_proposal_plugin_id")
        )
        self.override_proposal_plugin_id = None if override_proposal_plugin_id is None else (
            require_ascii_id(override_proposal_plugin_id, field_name="override_proposal_plugin_id")
        )

    def plan(
        self,
        candidates: tuple[ActionProposal, ...],
        source: DecisionDataSource | None,
        shared_memory: SharedMemory | None,
        *,
        frame_id: str,
        timestamp_ms: int,
    ) -> ActionPlan:
        selected = None
        if self.override_proposal_plugin_id is not None:
            selected = _active(candidates, self.override_proposal_plugin_id, frame_id)
        if selected is None:
            target = self.proposal_plugin_id
            if self.view_static_proposal_plugin_id is not None and _view_static(source):
                target = self.view_static_proposal_plugin_id
            selected = _active(candidates, target, frame_id)
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


def _active(
    candidates: tuple[ActionProposal, ...], plugin_id: str, frame_id: str,
) -> ActionProposal | None:
    return next((candidate for candidate in candidates
                 if candidate.plugin_id == plugin_id
                 and candidate.is_active(frame_id)), None)


def _view_static(source: DecisionDataSource | None) -> bool:
    """True only when this frame's observation reports ``view_static`` as ``True``."""

    if source is None or source.observation.status != "ready":
        return False
    return any(
        signal.get("signal_id") == "view_static" and signal.get("value") is True
        for signal in source.observation.value.signals
    )
