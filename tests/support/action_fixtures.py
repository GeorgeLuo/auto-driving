"""Step runners for tests: fixed-control actions, callable proposals, and a plan.

None of them is a packaged plugin, so a test holds whatever the catalog offers.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable

from autonomy.decision_cycle.action.runner import ActionRunner
from autonomy.decision_cycle.action.values import ActionDecision
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.cycle import DecisionSteps
from autonomy.decision_cycle.plan.runner import PlanRunner
from autonomy.decision_cycle.plan.values import ActionPlan, PlanContribution
from autonomy.decision_cycle.proposal.runner import ProposalRunner
from autonomy.decision_cycle.proposal.values import ActionProposal
from autonomy.decision_cycle.records import DecisionRecords
from autonomy.decision_cycle.steps import decision_steps
from autonomy.runtime.control import AutonomyControl


class FixedAction:
    """Action plugin that authorizes ``control`` for every cycle without applying a proposal."""

    def __init__(self, control: AutonomyControl, plugin_id: str = "test") -> None:
        self.control = control
        self.plugin_id = plugin_id

    def decide(self, context, plan, *, error_reason=None) -> ActionDecision:
        return ActionDecision(self.control)


def fixed_action_runner(control: AutonomyControl, *, plugin_id: str = "test") -> ActionRunner:
    return ActionRunner.from_plugins({plugin_id: FixedAction(control, plugin_id)})


def fixed_control_steps(
    control: AutonomyControl,
    *,
    plugin_id: str = "test",
    **steps: Any,
) -> DecisionSteps:
    """Steps whose action authorizes ``control``; ``steps`` override slots.

    A plan is present so a missing plan does not fail the action closed.
    """

    plan = steps.pop("plan", None) or plan_runner()
    return replace(
        decision_steps(),
        action=fixed_action_runner(control, plugin_id=plugin_id),
        plan=plan,
        **steps,
    )


class CallableProposal:
    """Proposal plugin that delegates ``propose`` to a function."""

    def __init__(self, plugin_id: str, function: Callable[..., Any], **attributes: Any) -> None:
        self.plugin_id = plugin_id
        self._function = function
        for name, value in attributes.items():
            setattr(self, name, value)

    def propose(self, context):
        return self._function(context)


def proposal_runner(
    plugins: dict[str, Callable[..., Any]], **attributes: Any
) -> ProposalRunner:
    """A proposal runner over ``plugin_id -> propose(context)`` functions."""

    return ProposalRunner.from_plugins(
        {
            plugin_id: CallableProposal(plugin_id, function, **attributes)
            for plugin_id, function in plugins.items()
        }
    )


class InactiveProposal:
    """Proposal plugin that proposes nothing, as an inactive candidate."""

    plugin_id = "inactive"

    def propose(self, context) -> ActionProposal:
        return ActionProposal(
            plugin_id=self.plugin_id, frame_id=context.frame_id, lifecycle="inactive",
            freshness="none", confidence=0.0, reason="test", command=None,
        )


class FirstActivePlan:
    """Plan plugin that selects the first active candidate, else idles."""

    plugin_id = "first_active"

    def plan(self, context, candidates) -> ActionPlan:
        selected = next((item for item in candidates if item.is_active(context.frame_id)), None)
        return ActionPlan(
            frame_id=context.frame_id,
            timestamp_ms=context.timestamp_ms,
            status="idle" if selected is None else "selected",
            candidates=tuple(candidates),
            selected_proposal_id=None if selected is None else selected.proposal_id,
            contributions=() if selected is None else (PlanContribution(
                proposal_id=selected.proposal_id, plugin_id=selected.plugin_id, weight=1.0, role="selected",
            ),),
            command=None if selected is None else selected.command,
            reason="" if selected is None else selected.reason,
            confidence=None if selected is None else selected.confidence,
            selector_id=self.plugin_id,
            metadata={},
        )


INACTIVE_PROPOSAL_SPEC = "tests.support.action_fixtures:InactiveProposal"
FIRST_ACTIVE_PLAN_SPEC = "tests.support.action_fixtures:FirstActivePlan"


def plan_runner() -> PlanRunner:
    """A plan that selects the first active candidate. Plan has no framework built-in."""

    return PlanRunner.from_plugins({FirstActivePlan.plugin_id: FirstActivePlan()})


def action_runner(plugin: Any | None = None) -> ActionRunner:
    if plugin is None:
        return decision_steps().action
    return ActionRunner.from_plugins({plugin.plugin_id: plugin})


class DecisionChain:
    """Run proposal, plan, and action runners for one frame, as the cycle would."""

    def __init__(
        self,
        proposal: ProposalRunner,
        *,
        plan: PlanRunner | None = None,
        action: ActionRunner | None = None,
    ) -> None:
        self.proposal = proposal
        self.plan = plan or plan_runner()
        self.action = action or action_runner()

    @property
    def plugins(self) -> dict[str, Any]:
        return self.proposal.plugins

    def run(
        self,
        *,
        frame_id: str,
        frame_index: int,
        timestamp_ms: int,
        shared_memory: dict[str, Any] | None = None,
    ) -> DecisionRecords:
        if shared_memory is None:
            shared_memory = {}
        context = DecisionFrameContext(
            frame_id=frame_id, frame_index=frame_index, timestamp_ms=timestamp_ms,
            shared_memory=shared_memory,
        )
        proposal = self.proposal(context)
        plan = self.plan(context, proposal)
        return DecisionRecords(proposal, plan, self.action(context, proposal, plan))


def decision_chain(
    *,
    plugins: dict[str, Callable[..., Any]],
    action: Any | None = None,
    **attributes: Any,
) -> DecisionChain:
    """A chain over test ``propose`` functions and an optional action plugin."""

    return DecisionChain(
        proposal_runner(plugins, **attributes),
        action=None if action is None else action_runner(action),
    )
