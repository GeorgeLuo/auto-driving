"""Cycle input for one tick."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from autonomy.shared_memory import SharedMemory
from autonomy.vehicle import SensorFrame


# Input fields shared by live publication, recording, and replay.
RECORDED_CONTEXT_FIELDS = ("timestamp_ms",)


def recorded_context(data: dict[str, Any]) -> dict[str, Any]:
    """The cycle inputs shared by live publication and recording."""
    return {key: data[key] for key in RECORDED_CONTEXT_FIELDS if key in data}


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
    metadata: dict[str, Any] = field(default_factory=dict)
    shared_memory: SharedMemory | None = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame_id": self.frame_id,
            "frame_index": self.frame_index,
            "timestamp_ms": self.timestamp_ms,
            "sensor_frame": self.sensor_frame.to_dict() if self.sensor_frame is not None else None,
            "metadata": self.metadata,
        }

    @classmethod
    def from_record(cls, data: dict[str, Any], **inputs: Any) -> DecisionFrameContext:
        """Restore cycle inputs using the same fields live publication records."""
        return cls(**{**recorded_context(data), **inputs})


__all__ = ["DecisionFrameContext"]
