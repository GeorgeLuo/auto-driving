from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from autonomy.vehicle import clamp_unit


@dataclass(frozen=True)
class AutonomyControl:
    """Normalized signed steering and throttle for every vehicle."""

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
