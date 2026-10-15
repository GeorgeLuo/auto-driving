from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from autonomy.vehicle import clamp_unit


@dataclass(frozen=True)
class AutonomyControl:
    """Normalized signed steering and throttle for every vehicle."""

    steering: float = 0.0
    throttle: float = 0.0
    confidence: float | None = None
    reason: str = "idle"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "steering", clamp_unit(self.steering))
        object.__setattr__(self, "throttle", clamp_unit(self.throttle))
        confidence = self.confidence
        if confidence is not None:
            confidence = max(0.0, clamp_unit(confidence))
        object.__setattr__(self, "confidence", confidence)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
