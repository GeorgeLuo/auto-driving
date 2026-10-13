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
from autonomy.decision_cycle.activation import STEPS
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

    ``duration_ms`` is the wall-clock total, ``completed_at_ms - started_at_ms``.
    ``timing`` is the monotonic span of ``run`` and of each step, with each
    applied plugin's duration copied from that step's plugin report.
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

    @property
    def duration_ms(self) -> int:
        return self.completed_at_ms - self.started_at_ms

    def control_record(self) -> dict[str, Any]:
        """Authorized output and its actual runtime delivery, on every host."""
        return {
            **self.control.to_dict(),
            "applied": self.application.applied if self.application is not None else False,
            "application": self.application.to_dict() if self.application is not None else None,
        }

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
        started = time.perf_counter()
        spans: dict[str, dict[str, Any]] = {}

        def run_step(name: str, runner: Any, call: Callable[[], Any]) -> Any:
            if runner is None:
                spans[name] = {"duration_ms": None, "plugins": []}
                return None
            step_started = time.perf_counter()
            try:
                return call()
            finally:
                spans[name] = {
                    "duration_ms": _span_ms(step_started),
                    "plugins": _timing_plugins(runner),
                }

        perception = run_step(
            "perception",
            steps.perception,
            lambda: _checked("perception", steps.perception(context), PerceptionText),
        )
        observation = run_step(
            "observation",
            steps.observation,
            lambda: _checked(
                "observation", steps.observation(context, perception), Observation
            ),
        )

        def _memory() -> dict[str, Any] | None:
            try:
                # Memory plugins write the host map; the step returns a report.
                memory = steps.memory(context, observation)
                if memory is not None and not isinstance(memory, dict):
                    raise TypeError("memory step must return a report dict or None")
                return memory
            except MemoryUpdateError:
                raise
            except Exception as exc:
                try:
                    detail = str(exc)
                except Exception:
                    detail = "unprintable error"
                raise MemoryUpdateError(f"{type(exc).__name__}: {detail}") from exc

        memory = run_step("memory", steps.memory, _memory)
        proposal = run_step(
            "proposal",
            steps.proposal,
            lambda: _checked(
                "proposal", steps.proposal(context, observation), ProposalResult
            ),
        )
        plan = run_step(
            "plan",
            steps.plan,
            lambda: _checked("plan", steps.plan(context, proposal), ActionPlan),
        )
        action = run_step(
            "action",
            steps.action,
            lambda: _checked(
                "action",
                steps.action(context, proposal, plan),
                ActionResult,
            ),
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
            timing={
                "duration_ms": _span_ms(started),
                "steps": {name: spans[name] for name in STEPS},
            },
        )


def _span_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000.0, 3)


def _timing_plugins(runner: Any) -> list[dict[str, Any]]:
    """The step's plugin report, reduced to identity, duration, and error."""

    report_for = getattr(runner, "plugin_report", None)
    if not callable(report_for):
        return []
    payload = report_for()
    plugins = payload.get("plugins") if isinstance(payload, dict) else ()
    return [
        {
            "plugin_id": item.get("plugin_id"),
            "duration_ms": item.get("duration_ms"),
            "error": item.get("error"),
        }
        for item in plugins
        if isinstance(item, dict)
    ]


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
