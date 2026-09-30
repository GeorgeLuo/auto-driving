from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from autonomy.vehicle import clamp_unit

if TYPE_CHECKING:
    from autonomy.decision_cycle.context import DecisionFrameContext
    from autonomy.decision_cycle.result import ActionResult


@dataclass(frozen=True)
class AutonomyControl:
    """Normalized pilot output consumed by Donkey DriveMode."""

    steering: float = 0.0
    throttle: float = 0.0
    confidence: float = 0.0
    reason: str = "idle"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "steering", clamp_unit(self.steering))
        object.__setattr__(self, "throttle", clamp_unit(self.throttle))
        object.__setattr__(self, "confidence", max(0.0, clamp_unit(self.confidence)))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class IdleAutonomyEngine:
    """Stable default engine: it proposes nothing, so the cycle holds position."""

    def reset(self) -> None:
        return None

    def describe_schema(self) -> dict[str, Any]:
        return {
            "schema": "autonomy_engine_schema_v0",
            "engine_id": "idle",
            "engine_spec": f"{self.__class__.__module__}:{self.__class__.__name__}",
            "purpose": "Safe default that always holds position.",
            "inputs": ["context", "perception", "observation", "memory"],
            "output": {
                "type": "ActionResult | None",
                "movement": "always idle",
            },
            "steps": {
                "action": "hold_position",
                "memory": "inspectable_snapshot",
            },
        }

    def act(
        self,
        context: "DecisionFrameContext",
        perception: Any,
        observation: Any,
        memory: Any,
    ) -> "ActionResult | None":
        return None


@runtime_checkable
class AutonomyEngine(Protocol):
    """Standard onboard controller shape for loadable autonomy engines.

    ``act`` is the decision cycle's action step: it returns the cycle's
    ``ActionResult``, or ``None`` when the engine takes no action.
    """

    def reset(self) -> None:
        ...

    def describe_schema(self) -> dict[str, Any]:
        ...

    def act(
        self,
        context: "DecisionFrameContext",
        perception: Any,
        observation: Any,
        memory: Any,
    ) -> "ActionResult | None":
        ...
