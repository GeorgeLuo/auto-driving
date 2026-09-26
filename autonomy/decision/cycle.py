from __future__ import annotations

import time
from copy import deepcopy
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Callable

from autonomy.perception import PerceptionText
from autonomy.runtime.engine import AutonomyControl
from autonomy.vehicle import SensorSnapshot

from .memory import MemorySnapshot
from .observation import Observation, observation_from_perception


DECISION_CYCLE_RESULT_SCHEMA = "decision_cycle_result_v0"


def timestamp_ms() -> int:
    return int(time.time() * 1000)


@dataclass(frozen=True)
class DecisionFrameContext:
    """Inputs and metadata for one controller-cycle tick."""

    frame_id: str
    frame_index: int
    timestamp_ms: int
    sensor_snapshot: SensorSnapshot | None = None
    mode: str = "autonomy"
    user_steering: float = 0.0
    user_throttle: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame_id": self.frame_id,
            "frame_index": self.frame_index,
            "timestamp_ms": self.timestamp_ms,
            "sensor_snapshot": self.sensor_snapshot.to_dict() if self.sensor_snapshot is not None else None,
            "mode": self.mode,
            "user_steering": self.user_steering,
            "user_throttle": self.user_throttle,
            "metadata": deepcopy(self.metadata),
        }


PerceiveStage = Callable[[DecisionFrameContext], PerceptionText | None]
ObserveStage = Callable[[DecisionFrameContext, PerceptionText | None], Observation | None]
MemoryStage = Callable[
    [DecisionFrameContext, Observation | None],
    MemorySnapshot | None,
]
ActionStage = Callable[
    [
        DecisionFrameContext,
        PerceptionText | None,
        Observation | None,
        MemorySnapshot | None,
    ],
    AutonomyControl | None,
]


@dataclass(frozen=True)
class DecisionStages:
    """Optional stages; memory plugins own retained-evidence transformations."""

    perceive: PerceiveStage | None = None
    observe: ObserveStage | None = None
    remember: MemoryStage | None = None
    choose_action: ActionStage | None = None


@dataclass(frozen=True)
class DecisionCycleResult:
    """Inspectable result of one controller-cycle tick."""

    context: DecisionFrameContext
    perception: PerceptionText | None
    observation: Observation | None
    memory: MemorySnapshot | None
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
            "control": self.control.to_dict(),
        }


class DecisionCycle:
    """No-op friendly staged controller cycle."""

    def __init__(
        self,
        stages: DecisionStages | None = None,
        *,
        idle_reason: str = "decision-cycle-idle",
    ) -> None:
        self.stages = stages or DecisionStages()
        self.idle_reason = idle_reason

    def run(self, context: DecisionFrameContext) -> DecisionCycleResult:
        started_at_ms = timestamp_ms()
        perception = self.stages.perceive(context) if self.stages.perceive else None
        if self.stages.observe:
            observation = self.stages.observe(context, perception)
        elif perception is not None:
            observation = observation_from_perception(
                observation_id=context.frame_id,
                sensor_snapshot=context.sensor_snapshot,
                perception=perception,
                metadata={"source": "default_observe_stage"},
            )
        else:
            observation = None
        memory = self.stages.remember(context, observation) if self.stages.remember else None
        if memory is not None and not isinstance(memory, MemorySnapshot):
            raise TypeError(
                "decision memory stage must return MemorySnapshot or None"
            )
        control = (
            self.stages.choose_action(context, perception, observation, memory)
            if self.stages.choose_action
            else None
        )
        if control is None:
            control = AutonomyControl(confidence=1.0, reason=self.idle_reason)
        elif not isinstance(control, AutonomyControl):
            raise TypeError("decision action stage must return AutonomyControl or None")

        return DecisionCycleResult(
            context=context,
            perception=perception,
            observation=observation,
            memory=memory,
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
