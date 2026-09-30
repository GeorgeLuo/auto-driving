"""Decision-cycle ordering and the records passed between its operations.

``perceive`` returns current evidence. ``observe`` adapts that evidence and
the sensor context into the current-frame record. ``remember`` returns
retained evidence. ``act`` proposes, plans, and gates the cycle's action and
returns an ``ActionResult`` whose control the cycle applies.
``shared_memory`` on the frame context is the host-owned map.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any, Callable

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.memory.errors import MemoryUpdateError
from autonomy.decision_cycle.memory.publication import observation_after_memory
from autonomy.decision_cycle.observation.step import observation_from_perception
from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.memory.snapshots.values import MemorySnapshot
from autonomy.decision_cycle.perception.interface import PerceptionText
from autonomy.decision_cycle.result import ActionResult
from autonomy.runtime.engine import AutonomyControl


DECISION_CYCLE_RESULT_SCHEMA = "decision_cycle_result_v0"


def timestamp_ms() -> int:
    return int(time.time() * 1000)


PerceiveStep = Callable[[DecisionFrameContext], PerceptionText | None]
ObserveStep = Callable[[DecisionFrameContext, PerceptionText | None], Observation | None]
MemoryStep = Callable[
    [DecisionFrameContext, Observation | None],
    MemorySnapshot | None,
]
ActionStep = Callable[
    [
        DecisionFrameContext,
        PerceptionText | None,
        Observation | None,
        MemorySnapshot | None,
    ],
    ActionResult | None,
]


@dataclass(frozen=True)
class DecisionSteps:
    """The cycle operations.

    ``perceive``, ``observe``, and ``remember`` are the perception, observation,
    and memory steps. ``act`` is the action composition. ``observe``
    overrides the default adaptation of perception evidence into the
    current-frame record. Omitting ``observe`` leaves that default in place
    when perception evidence is present.
    """

    perceive: PerceiveStep | None = None
    observe: ObserveStep | None = None
    remember: MemoryStep | None = None
    act: ActionStep | None = None


@dataclass(frozen=True)
class DecisionCycleResult:
    """Records from one cycle tick.

    ``perception`` is current evidence, ``observation`` is the current-frame
    record, and ``memory`` is retained evidence.
    """

    context: DecisionFrameContext
    perception: PerceptionText | None
    observation: Observation | None
    # Retained evidence also published at shared_memory["decision.snapshot"].
    memory: MemorySnapshot | None
    action: ActionResult | None
    control: AutonomyControl
    started_at_ms: int
    completed_at_ms: int
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
            "perception": self.perception.to_dict() if self.perception is not None else None,
            "observation": self.observation.to_dict() if self.observation is not None else None,
            "memory": _to_plain_data(self.memory),
            "action": self.action.to_dict() if self.action is not None else None,
            "control": self.control.to_dict(),
        }


class DecisionCycle:
    """Run perceive, observe, remember, then act.

    ``observe`` overrides the default adaptation. Omitting it leaves that
    default in place when perception evidence is present. ``remember`` returns
    a ``MemorySnapshot`` or ``None``. A memory plugin may still replace the
    current observation through ``shared_memory["decision.observation"]``.
    """

    def __init__(
        self,
        steps: DecisionSteps | None = None,
        *,
        idle_reason: str = "decision-cycle-idle",
    ) -> None:
        self.steps = steps or DecisionSteps()
        self.idle_reason = idle_reason

    def run(self, context: DecisionFrameContext) -> DecisionCycleResult:
        started_at_ms = timestamp_ms()
        perception = self.steps.perceive(context) if self.steps.perceive else None
        if self.steps.observe:
            observation = self.steps.observe(context, perception)
        elif perception is not None:
            observation = observation_from_perception(
                observation_id=context.frame_id,
                sensor_snapshot=context.sensor_snapshot,
                perception=perception,
                metadata={"source": "default_observe_step"},
            )
        else:
            observation = None
        try:
            # Step return is retained evidence, also stored at
            # shared_memory["decision.snapshot"] by the implementation.
            memory = self.steps.remember(context, observation) if self.steps.remember else None
            if memory is not None and not isinstance(memory, MemorySnapshot):
                raise TypeError("decision memory step must return MemorySnapshot or None")
            if memory is not None and memory.health == "error":
                raise MemoryUpdateError(memory.error or "memory step returned an error snapshot")
        except MemoryUpdateError:
            raise
        except Exception as exc:
            try:
                detail = str(exc)
            except Exception:
                detail = "unprintable error"
            raise MemoryUpdateError(f"{type(exc).__name__}: {detail}") from exc
        observation = observation_after_memory(context.shared_memory, observation, memory)
        action = (
            self.steps.act(context, perception, observation, memory)
            if self.steps.act
            else None
        )
        if action is None:
            control = AutonomyControl(confidence=1.0, reason=self.idle_reason)
        elif isinstance(action, ActionResult):
            control = action.control
        else:
            raise TypeError("decision action step must return ActionResult or None")

        return DecisionCycleResult(
            context=context,
            perception=perception,
            observation=observation,
            memory=memory,
            action=action,
            control=control,
            started_at_ms=started_at_ms,
            completed_at_ms=timestamp_ms(),
        )


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
