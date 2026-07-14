from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol, runtime_checkable


def clamp_unit(value: float) -> float:
    return max(-1.0, min(1.0, float(value)))


@dataclass(frozen=True)
class AutonomySnapshot:
    """Inputs made available to one onboard autonomy engine step."""

    sensor_snapshot: Any = None
    perception: Any = None
    observation: Any = None
    cycle: dict[str, Any] = field(default_factory=dict)
    mode: str = "user"
    user_steering: float = 0.0
    user_throttle: float = 0.0
    timestamp_ms: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


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
        object.__setattr__(self, "confidence", max(0.0, min(1.0, float(self.confidence))))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class IdleAutonomyEngine:
    """Stable default engine that always holds position."""

    def __init__(self, reason: str = "stable-idle-engine") -> None:
        self.reason = reason

    def reset(self) -> None:
        return None

    def describe_schema(self) -> dict[str, Any]:
        return {
            "schema": "autonomy_engine_schema_v0",
            "engine_id": "idle",
            "engine_spec": f"{self.__class__.__module__}:{self.__class__.__name__}",
            "purpose": "Safe default that always holds position.",
            "inputs": [
                "sensor_snapshot",
                "perception",
                "observation",
                "cycle",
                "mode",
                "user_steering",
                "user_throttle",
            ],
            "output": {
                "type": "AutonomyControl",
                "movement": "always idle",
            },
            "stages": {
                "action": "hold_position",
                "memory": None,
                "patterns": None,
                "projections": None,
            },
        }

    def step(self, snapshot: AutonomySnapshot) -> AutonomyControl:
        return AutonomyControl(
            steering=0.0,
            throttle=0.0,
            confidence=1.0,
            reason=self.reason,
            metadata={
                "mode": snapshot.mode,
                "has_sensor_snapshot": snapshot.sensor_snapshot is not None,
                "has_perception": snapshot.perception is not None,
                "has_observation": snapshot.observation is not None,
            },
        )


@runtime_checkable
class AutonomyEngine(Protocol):
    """Standard onboard controller shape for loadable autonomy engines."""

    def reset(self) -> None:
        ...

    def describe_schema(self) -> dict[str, Any]:
        ...

    def step(self, snapshot: AutonomySnapshot) -> AutonomyControl:
        ...
