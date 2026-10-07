"""The decision cycle: six steps run in order, each recording one output.

A step is a slot in ``DecisionSteps``, named by the same noun as its package,
its plugin manager, its activation (``runtime/<step>/active.json``), and its
field in ``DecisionCycleResult``. Each slot holds that step's runner, which
runs the plugins selected for the step:

- ``perception(context)`` returns current evidence.
- ``observation(context, perception)`` returns the current-frame record.
- ``memory(context, observation)`` lets memory plugins update the host map
  and returns their report.
- ``proposal(context, observation)`` returns the candidates.
- ``plan(context, proposal)`` returns the plan over those candidates.
- ``action(context, proposal, plan)`` returns the authorized control.

An empty slot records ``None``. ``shared_memory`` on the frame context is the
host-owned map every step's plugins share.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any, Callable

from autonomy.decision_cycle.action.result import ActionResult
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.memory.errors import MemoryUpdateError
from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.perception.interface import PerceptionText
from autonomy.decision_cycle.plan.values import ActionPlan
from autonomy.decision_cycle.proposal.result import ProposalResult
from autonomy.runtime.control import AutonomyControl
from autonomy.runtime.execution import ControlApplication


DECISION_CYCLE_RESULT_SCHEMA = "decision_cycle_result_v1"


def timestamp_ms() -> int:
    return int(time.time() * 1000)


PerceptionStep = Callable[[DecisionFrameContext], PerceptionText | None]
ObservationStep = Callable[[DecisionFrameContext, PerceptionText | None], Observation | None]
MemoryStep = Callable[[DecisionFrameContext, Observation | None], dict[str, Any] | None]
ProposalStep = Callable[[DecisionFrameContext, Observation | None], ProposalResult | None]
PlanStep = Callable[[DecisionFrameContext, ProposalResult | None], ActionPlan | None]
ActionStep = Callable[
    [DecisionFrameContext, ProposalResult | None, ActionPlan | None],
    ActionResult | None,
]


@dataclass(frozen=True)
class DecisionSteps:
    """The cycle's steps, in the order they run."""

    perception: PerceptionStep | None = None
    observation: ObservationStep | None = None
    memory: MemoryStep | None = None
    proposal: ProposalStep | None = None
    plan: PlanStep | None = None
    action: ActionStep | None = None


@dataclass(frozen=True)
class DecisionCycleResult:
    """One cycle's records, one per step, and the authorized control.

    ``memory`` is the memory step's report of plugin state, for diagnostics;
    plugins read what memory published in the host map.
    """

    context: DecisionFrameContext
    perception: PerceptionText | None
    observation: Observation | None
    memory: dict[str, Any] | None
    proposal: ProposalResult | None
    plan: ActionPlan | None
    action: ActionResult | None
    control: AutonomyControl
    started_at_ms: int
    completed_at_ms: int
    application: ControlApplication | None = None
    schema: str = DECISION_CYCLE_RESULT_SCHEMA

    @property
    def duration_ms(self) -> int:
        return self.completed_at_ms - self.started_at_ms

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "started_at_ms": self.started_at_ms,
            "completed_at_ms": self.completed_at_ms,
            "duration_ms": self.duration_ms,
            "context": self.context.to_dict(),
            "perception": _record(self.perception),
            "observation": _record(self.observation),
            "memory": _to_plain_data(self.memory),
            "proposal": _record(self.proposal),
            "plan": _record(self.plan),
            "action": _record(self.action),
            "control": self.control.to_dict(),
            "application": self.application.to_dict() if self.application is not None else None,
        }


class DecisionCycle:
    """Run the configured steps in order and record each output."""

    def __init__(
        self,
        steps: DecisionSteps | None = None,
        *,
        idle_reason: str = "decision-cycle-idle",
    ) -> None:
        self.steps = steps or DecisionSteps()
        self.idle_reason = idle_reason

    def run(self, context: DecisionFrameContext) -> DecisionCycleResult:
        steps = self.steps
        started_at_ms = timestamp_ms()
        perception = _checked(
            "perception",
            steps.perception(context) if steps.perception else None,
            PerceptionText,
        )
        observation = _checked(
            "observation",
            steps.observation(context, perception) if steps.observation else None,
            Observation,
        )
        try:
            # Memory plugins write the host map; the step returns a report.
            memory = steps.memory(context, observation) if steps.memory else None
            if memory is not None and not isinstance(memory, dict):
                raise TypeError("memory step must return a report dict or None")
        except MemoryUpdateError:
            raise
        except Exception as exc:
            try:
                detail = str(exc)
            except Exception:
                detail = "unprintable error"
            raise MemoryUpdateError(f"{type(exc).__name__}: {detail}") from exc
        proposal = _checked(
            "proposal",
            steps.proposal(context, observation) if steps.proposal else None,
            ProposalResult,
        )
        plan = _checked(
            "plan",
            steps.plan(context, proposal) if steps.plan else None,
            ActionPlan,
        )
        action = _checked(
            "action",
            steps.action(context, proposal, plan) if steps.action else None,
            ActionResult,
        )
        control = (
            action.control
            if action is not None
            else AutonomyControl(confidence=1.0, reason=self.idle_reason)
        )
        return DecisionCycleResult(
            context=context,
            perception=perception,
            observation=observation,
            memory=memory,
            proposal=proposal,
            plan=plan,
            action=action,
            control=control,
            started_at_ms=started_at_ms,
            completed_at_ms=timestamp_ms(),
        )


def _checked(step: str, record: Any, record_type: type) -> Any:
    if record is not None and not isinstance(record, record_type):
        raise TypeError(f"{step} step must return {record_type.__name__} or None")
    return record


def _record(value: Any) -> Any:
    return value.to_dict() if value is not None else None


def _to_plain_data(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return value.to_dict()
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return {str(key): _to_plain_data(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_plain_data(item) for item in value]
    return value
