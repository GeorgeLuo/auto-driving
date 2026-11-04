"""Run the proposal, plan, and action steps for one recorded frame.

``DecisionRecords`` lives in ``autonomy.decision_cycle.records``. It holds the
three records and answers the questions the decision tools ask of a frame.
``DecisionRunners`` builds those steps' runners from activation payloads and
runs them, as the cycle would after memory. A payload a vehicle recorded runs
on the controller release it names, as in every replay.
"""

from __future__ import annotations

from typing import Any, Mapping

from autonomy.decision_cycle.action.runner import ActionRunner
from autonomy.decision_cycle.activation import (
    DECISION_STEPS,
    StepActivation,
    step_activation_from_payload,
)
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.plan.runner import PlanRunner
from autonomy.decision_cycle.proposal.runner import ProposalRunner
from autonomy.decision_cycle.records import DecisionRecords
from autonomy.decision_cycle.steps import builtin_activation

from ...replay.selections import recorded_runner


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
            if activation.step != step:
                raise ValueError(f"activation is for step {activation.step!r}, not {step!r}")
            return activation

        return cls(
            recorded_runner(activation_for("proposal")),
            recorded_runner(activation_for("plan")),
            recorded_runner(activation_for("action")),
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
        shared_memory: dict[str, Any] | None = None,
    ) -> DecisionRecords:
        if shared_memory is None:
            shared_memory = {}
        context = DecisionFrameContext(
            frame_id=frame_id,
            frame_index=frame_index,
            timestamp_ms=timestamp_ms,
            shared_memory=shared_memory,
        )
        proposal = self.proposal(context)
        plan = self.plan(context, proposal)
        return DecisionRecords(proposal, plan, self.action(context, proposal, plan))
