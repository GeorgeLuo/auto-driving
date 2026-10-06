"""Cycle input for one tick."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from autonomy.shared_memory import SharedMemory
from autonomy.vehicle import SensorFrame


@dataclass(frozen=True)
class DecisionFrameContext:
    """Inputs for one cycle tick.

    ``shared_memory`` is the host-owned map that perception, memory, and
    proposal plugins read and write.
    """

    frame_id: str
    frame_index: int
    timestamp_ms: int
    sensor_frame: SensorFrame | None = None
    mode: str = "autonomy"
    user_steering: float = 0.0
    user_throttle: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    shared_memory: SharedMemory | None = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame_id": self.frame_id,
            "frame_index": self.frame_index,
            "timestamp_ms": self.timestamp_ms,
            "sensor_frame": self.sensor_frame.to_dict() if self.sensor_frame is not None else None,
            "mode": self.mode,
            "user_steering": self.user_steering,
            "user_throttle": self.user_throttle,
            "metadata": deepcopy(self.metadata),
        }


__all__ = ["DecisionFrameContext"]
