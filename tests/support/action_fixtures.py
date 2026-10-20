"""Step runners for tests: fixed-control actions and callable proposals."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable

from autonomy.decision_cycle.action.result import ActionResult
from autonomy.decision_cycle.action.runner import ActionRunner
from autonomy.decision_cycle.action.values import ActionDecision
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.cycle import DecisionSteps
from autonomy.decision_cycle.plan.values import ActionPlan
from autonomy.decision_cycle.proposal.result import ProposalResult
from autonomy.decision_cycle.plan.runner import PlanRunner
from autonomy.decision_cycle.proposal.runner import ProposalRunner
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

    The packaged plan is present so a missing plan does not fail the action closed.
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


def plan_runner() -> PlanRunner:
    """The packaged highest-confidence plan. Plan has no framework built-in."""

    from implementations.decision_cycle.catalog import packaged_activation

    return PlanRunner.from_activation(packaged_activation("plan"))


def action_runner(plugin: Any | None = None) -> ActionRunner:
    if plugin is None:
        return decision_steps().action
    return ActionRunner.from_plugins({plugin.plugin_id: plugin})


@dataclass(frozen=True)
class DecisionRecords:
    """The proposal, plan, and action records of one cycle."""

    proposal: ProposalResult
    plan: ActionPlan | None
    action: ActionResult

    @property
    def status(self) -> str:
        return self.action.status

    @property
    def reason(self) -> str:
        return self.action.reason


    @property
    def authority(self):
        return self.action.authority

    @property
    def control(self) -> AutonomyControl:
        return self.action.control


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


def packaged_decision_chain(activation=None, *, action: Any | None = None) -> DecisionChain:
    """A chain over the packaged proposal selection (or ``activation``)."""

    from implementations.decision_cycle.catalog import packaged_activation

    return DecisionChain(
        ProposalRunner.from_activation(activation or packaged_activation("proposal")),
        action=None if action is None else action_runner(action),
    )
