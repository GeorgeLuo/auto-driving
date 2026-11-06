"""The decision cycle: six steps run in order, each recording one output.

A step is a slot in ``DecisionSteps``, named by the same noun as its package,
its plugin manager, its activation (``runtime/<step>/active.json``), and its
field in ``DecisionCycleResult``. Each slot holds that step's runner, which
runs the plugins selected for the step:

- ``perception(context)`` returns current evidence.
- ``observation(context, perception)`` returns the current-frame record.
- ``memory(context)`` lets memory plugins update the host map and returns
  their report. The cycle writes the observation at ``OBSERVATION_KEY``
  before memory runs, and removes it when there is none.
- ``proposal(context)`` returns the candidates.
- ``plan(context, proposal)`` returns the plan over those candidates.
- ``action(context, proposal, plan)`` returns the authorized control.

An empty slot records ``None``. ``shared_memory`` on the frame context is the
host-owned map every step's plugins share.
"""

from __future__ import annotations

import time
from copy import deepcopy
from dataclasses import asdict, dataclass, field, is_dataclass
from threading import Lock
from typing import Any, Callable

from autonomy.decision_cycle.action.result import ActionResult
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.memory.errors import MemoryUpdateError
from autonomy.decision_cycle.observation.publication import write_observation
from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.perception.interface import PerceptionText
from autonomy.decision_cycle.plan.values import ActionPlan
from autonomy.decision_cycle.proposal.result import ProposalResult
from autonomy.decision_cycle.timing import clock, cycle_timing, elapsed_ms
from autonomy.runtime.control import AutonomyControl
from autonomy.runtime.execution import ControlApplication

# One lock for every result. It stays out of the dataclass so copy, pickle,
# and asdict do not have to carry a lock.
_CYCLE_EXPORT_LOCK = Lock()


DECISION_CYCLE_RESULT_SCHEMA = "decision_cycle_result"


def timestamp_ms() -> int:
    return int(time.time() * 1000)


PerceptionStep = Callable[[DecisionFrameContext], PerceptionText | None]
ObservationStep = Callable[[DecisionFrameContext, PerceptionText | None], Observation | None]
MemoryStep = Callable[[DecisionFrameContext], dict[str, Any] | None]
ProposalStep = Callable[[DecisionFrameContext], ProposalResult | None]
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

    ``timing`` is ``cycle_timing``: the cycle's monotonic total, each step
    call's duration and each plugin's. ``duration_ms`` is that total; a result
    built without timing falls back to ``completed_at_ms - started_at_ms``.
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
    timing: dict[str, Any] | None = None
    schema: str = DECISION_CYCLE_RESULT_SCHEMA
    # Retained on this object only. ``replace`` builds a new result, so a host
    # can record context and application before the first export without
    # serializing the six records again.
    _cycle_export: dict[str, Any] | None = field(default=None, init=False, repr=False, compare=False)

    @property
    def duration_ms(self) -> float | int:
        if self.timing is not None:
            return self.timing["duration_ms"]
        return self.completed_at_ms - self.started_at_ms

    def control_record(self) -> dict[str, Any]:
        """Authorized output and its actual runtime delivery, on every host."""
        return {
            **self.control.to_dict(),
            "applied": self.application.applied if self.application is not None else False,
            "application": self.application.to_dict() if self.application is not None else None,
        }

    def export_cycle(self) -> dict[str, Any]:
        """Copy the cycle body, serializing its six records once for this result.

        The retained body stays on this object. Each caller gets a copy, so a
        publication cannot change a later recording or status read. Replacing
        context or application builds a new result; that result serializes when
        it is first exported, not when it is replaced.
        """

        with _CYCLE_EXPORT_LOCK:
            if self._cycle_export is None:
                object.__setattr__(self, "_cycle_export", deepcopy(export_cycle_body(self)))
            retained = self._cycle_export
        return deepcopy(retained)

    def to_dict(self) -> dict[str, Any]:
        cycle = self.export_cycle()
        return {
            "schema": self.schema,
            "started_at_ms": self.started_at_ms,
            "completed_at_ms": self.completed_at_ms,
            "duration_ms": self.duration_ms,
            "context": self.context.to_dict(),
            **cycle,
            "control": self.control.to_dict(),
            "application": self.application.to_dict() if self.application is not None else None,
            "timing": _to_plain_data(self.timing),
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
        started = clock()
        durations: dict[str, float | None] = {}
        perception = observation = memory = proposal = plan = action = None
        try:
            perception = _completed(
                durations, "perception", steps.perception, context, expected=PerceptionText,
            )
            observation = _completed(
                durations, "observation", steps.observation, context, perception,
                expected=Observation,
            )
            if context.shared_memory is not None:
                write_observation(context.shared_memory, observation)
            memory = _run_memory_step(durations, steps.memory, context)
            proposal = _completed(
                durations, "proposal", steps.proposal, context, expected=ProposalResult,
            )
            plan = _completed(
                durations, "plan", steps.plan, context, proposal, expected=ActionPlan,
            )
            action = _completed(
                durations, "action", steps.action, context, proposal, plan,
                expected=ActionResult,
            )
        except Exception as exc:
            # The raising step stays null: its duration is None and its output
            # was not assigned. Later steps never started, so they stay None and
            # report no plugins rather than an earlier cycle's.
            partial = _cycle_result(
                context,
                perception=perception,
                observation=observation,
                memory=memory,
                proposal=proposal,
                plan=plan,
                action=action,
                control=AutonomyControl(confidence=1.0, reason=self.idle_reason),
                started_at_ms=started_at_ms,
                durations=durations,
                started=started,
                steps=steps,
            )
            try:
                exc.cycle_result = partial
            except Exception:
                pass
            raise
        control = (
            action.control
            if action is not None
            else AutonomyControl(confidence=1.0, reason=self.idle_reason)
        )
        return _cycle_result(
            context,
            perception=perception,
            observation=observation,
            memory=memory,
            proposal=proposal,
            plan=plan,
            action=action,
            control=control,
            started_at_ms=started_at_ms,
            durations=durations,
            started=started,
            steps=steps,
        )


def _cycle_result(
    context: DecisionFrameContext,
    *,
    perception: PerceptionText | None,
    observation: Observation | None,
    memory: dict[str, Any] | None,
    proposal: ProposalResult | None,
    plan: ActionPlan | None,
    action: ActionResult | None,
    control: AutonomyControl,
    started_at_ms: int,
    durations: dict[str, float | None],
    started: float,
    steps: DecisionSteps,
) -> DecisionCycleResult:
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
        timing=cycle_timing(elapsed_ms(started), durations, steps),
    )


def cycle_result_of(exc: BaseException) -> DecisionCycleResult | None:
    """The partial result a raised cycle attached, or None when it attached none."""

    result = getattr(exc, "cycle_result", None)
    return result if isinstance(result, DecisionCycleResult) else None


def _run_memory_step(
    durations: dict[str, float | None], run: MemoryStep | None, context: DecisionFrameContext,
) -> dict[str, Any] | None:
    """Run memory, translating failures at the cycle boundary for any callable."""

    if not run:
        return None
    durations["memory"] = None
    started = clock()
    try:
        # Memory plugins write the host map; the step returns a report.
        memory = run(context)
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
    durations["memory"] = elapsed_ms(started)
    return memory


def _completed(durations: dict[str, float | None], step: str, run: Any, *args: Any, expected: type) -> Any:
    """``run(*args)`` once it returns a usable record. A raise leaves the duration None."""

    if not run:
        return None
    durations[step] = None
    started = clock()
    record = run(*args)
    if record is not None and not isinstance(record, expected):
        raise TypeError(f"{step} step must return {expected.__name__} or None")
    durations[step] = elapsed_ms(started)
    return record


CYCLE_RECORD_KEYS = (
    "perception", "observation", "memory", "proposal", "plan", "action",
)


def export_cycle_body(result: Any) -> dict[str, Any]:
    """The six cycle records on any completed result, each detached.

    Reports and recordings share this body. Each keeps its own identity,
    timing, and delivery fields beside it.
    """

    return {
        key: _to_plain_data(getattr(result, key, None))
        for key in CYCLE_RECORD_KEYS
    }


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
