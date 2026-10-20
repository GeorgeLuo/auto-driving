"""Cycle input for one tick."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from autonomy.runtime.execution import ControlApplication
from autonomy.shared_memory import SharedMemory
from autonomy.vehicle import SensorFrame


# Input fields shared by live publication, recording, and replay. The reset flag
# belongs only to delta-encoded recordings, not the live cycle context.
RECORDED_CONTEXT_FIELDS = ("mode", "user_steering", "user_throttle", "control_applications")
CONTROL_APPLICATION_RESET_KEY = "control_applications_reset"


def recorded_context(data: dict[str, Any]) -> dict[str, Any]:
    """Detach the cycle inputs shared by live publication and recording."""
    return deepcopy({
        key: data[key] for key in (*RECORDED_CONTEXT_FIELDS, CONTROL_APPLICATION_RESET_KEY)
        if key in data
    })


@dataclass(frozen=True)
class DecisionFrameContext:
    """Inputs for one cycle tick.

    ``shared_memory`` is the host-owned plugin map. ``control_applications``
    is the bounded window of target writes and their execution modes. Manual
    and observe-only applications do not describe operator motion. On Donkey,
    timestamps mark output mailbox updates; HostTelemetryStore observes the
    actual drivetrain tick. Wall-clock discontinuities remain unknown motion.
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
    control_applications: tuple[ControlApplication, ...] = field(default=(), repr=False, compare=False)

    def to_dict(self, *, include_control_applications: bool = True) -> dict[str, Any]:
        return {
            "frame_id": self.frame_id,
            "frame_index": self.frame_index,
            "timestamp_ms": self.timestamp_ms,
            "sensor_frame": self.sensor_frame.to_dict() if self.sensor_frame is not None else None,
            "mode": self.mode,
            "user_steering": self.user_steering,
            "user_throttle": self.user_throttle,
            "metadata": deepcopy(self.metadata),
            **({"control_applications": [item.to_dict() for item in self.control_applications]}
               if include_control_applications else {}),
        }

    @classmethod
    def from_record(cls, data: dict[str, Any], **inputs: Any) -> DecisionFrameContext:
        """Restore cycle inputs using the same fields live publication records."""
        if not isinstance(data, dict):
            raise ValueError("recorded context must be an object")
        values = {key: data[key] for key in RECORDED_CONTEXT_FIELDS if key in data}
        raw = values.get("control_applications", ())
        if not isinstance(raw, (list, tuple)):
            raise ValueError("control_applications must be a list")
        values["control_applications"] = tuple(ControlApplication.from_dict(item) for item in raw)
        if CONTROL_APPLICATION_RESET_KEY in data and type(data[CONTROL_APPLICATION_RESET_KEY]) is not bool:
            raise ValueError("control_applications_reset must be a boolean")
        return cls(**{**values, **inputs})


__all__ = ["DecisionFrameContext"]
