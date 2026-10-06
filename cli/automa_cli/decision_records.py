"""The proposal, plan, and action records of one cycle, for decision tools.

The decision tools (stream, apply, inspect, view) read the three decision
steps' records together. ``DecisionRecords`` holds them and answers the
questions the tools ask of a frame (its status, source, plan, and authority).
``DecisionRunners`` builds the three steps' runners from activation payloads
and runs them for one frame, as the cycle would after memory. It uses the
same staged-bundle loader as step info and the automation worker; recorded
bundle metadata selects staged plugin code rather than workspace code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from autonomy.decision_cycle.action.result import ActionResult
from autonomy.decision_cycle.action.runner import ActionRunner
from autonomy.decision_cycle.activation import (
    DECISION_STEPS,
    StepActivation,
    step_activation_from_payload,
)
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.plan.runner import PlanRunner
from autonomy.decision_cycle.plan.values import ActionPlan
from autonomy.decision_cycle.proposal.inputs import ComponentEnvelope, DecisionDataSource
from autonomy.decision_cycle.proposal.result import ProposalResult
from autonomy.decision_cycle.proposal.runner import ProposalRunner
from autonomy.decision_cycle.steps import builtin_activation
from autonomy.runtime.control import AutonomyControl

from .step_hosting import load_staged_runner


@dataclass(frozen=True)
class DecisionRecords:
    proposal: ProposalResult
    plan: ActionPlan | None
    action: ActionResult

    @property
    def frame_id(self) -> str:
        return self.action.frame_id

    @property
    def status(self) -> str:
        return self.action.status

    @property
    def reason(self) -> str:
        return self.action.reason

    @property
    def source(self) -> DecisionDataSource | None:
        return self.proposal.source

    @property
    def authority(self):
        return self.action.authority

    @property
    def control(self) -> AutonomyControl | None:
        return self.action.control

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal": self.proposal.to_dict(),
            "plan": self.plan.to_dict() if self.plan is not None else None,
            "action": self.action.to_dict(),
        }

    @classmethod
    def from_cycle(cls, cycle: Any) -> "DecisionRecords | None":
        """The decision records of a ``DecisionCycleResult``, when it has them."""

        proposal = getattr(cycle, "proposal", None)
        action = getattr(cycle, "action", None)
        if not isinstance(proposal, ProposalResult) or not isinstance(action, ActionResult):
            return None
        return cls(proposal=proposal, plan=getattr(cycle, "plan", None), action=action)


def activations_from_payloads(
    steps: Mapping[str, Any],
) -> dict[str, StepActivation | None]:
    """Decode the decision steps' activation payloads; a missing step is ``None``."""

    activations: dict[str, StepActivation | None] = {}
    for step in DECISION_STEPS:
        payload = steps.get(step) if isinstance(steps, Mapping) else None
        activations[step] = (
            None if payload is None else step_activation_from_payload(payload, step=step)
        )
    return activations


class DecisionRunners:
    """Proposal, plan, and action runners for replaying one frame at a time."""

    def __init__(
        self,
        proposal: ProposalRunner,
        plan: PlanRunner,
        action: ActionRunner,
    ) -> None:
        self.proposal = proposal
        self.plan = plan
        self.action = action

    @classmethod
    def from_activations(
        cls, activations: Mapping[str, StepActivation | None]
    ) -> "DecisionRunners":
        def activation_for(step: str) -> StepActivation:
            activation = activations.get(step) or builtin_activation(step)
            if activation is None:
                raise ValueError(f"no {step} activation is staged")
            return activation

        return cls(
            load_staged_runner(activation_for("proposal")),
            load_staged_runner(activation_for("plan")),
            load_staged_runner(activation_for("action")),
        )

    @classmethod
    def from_payloads(cls, steps: Mapping[str, Any]) -> "DecisionRunners":
        return cls.from_activations(activations_from_payloads(steps))

    def run(
        self,
        *,
        frame_id: str,
        frame_index: int,
        timestamp_ms: int,
        mode: str = "unknown",
        host_application: ComponentEnvelope | None = None,
        **proposal_inputs: Any,
    ) -> DecisionRecords:
        proposal = self.proposal.run(
            frame_id=frame_id,
            frame_index=frame_index,
            timestamp_ms=timestamp_ms,
            **proposal_inputs,
        )
        context = DecisionFrameContext(
            frame_id=frame_id,
            frame_index=frame_index,
            timestamp_ms=timestamp_ms,
            mode=mode,
            metadata={} if host_application is None else {"host_application": host_application},
        )
        plan = self.plan(context, proposal)
        return DecisionRecords(proposal, plan, self.action(context, proposal, plan))
