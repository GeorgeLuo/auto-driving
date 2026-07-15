from __future__ import annotations

import math
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


FRONT_CAMERA_SENSOR_ID = "front_camera"


def _finite_float(value: float, *, field_name: str) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be numeric") from exc
    if not math.isfinite(normalized):
        raise ValueError(f"{field_name} must be finite")
    return normalized


def clamp_unit(value: float) -> float:
    return max(-1.0, min(1.0, _finite_float(value, field_name="unit value")))


@dataclass(frozen=True)
class VehicleAction:
    """Executable RC-car-like input action."""

    forward: bool = False
    reverse: bool = False
    steering: float = 0.0

    def __post_init__(self) -> None:
        if self.forward and self.reverse:
            raise ValueError("VehicleAction cannot be both forward and reverse")
        object.__setattr__(self, "steering", clamp_unit(self.steering))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class VehiclePulse:
    """Timed execution envelope for a real vehicle action input."""

    action: VehicleAction = field(default_factory=VehicleAction)
    throttle: float = 0.0
    duration_s: float = 0.0
    settle_s: float = 0.0
    recording: bool = False
    label: str = "vehicle_pulse"

    def __post_init__(self) -> None:
        throttle = _finite_float(self.throttle, field_name="VehiclePulse.throttle")
        duration_s = _finite_float(self.duration_s, field_name="VehiclePulse.duration_s")
        settle_s = _finite_float(self.settle_s, field_name="VehiclePulse.settle_s")
        object.__setattr__(self, "throttle", max(0.0, min(1.0, throttle)))
        object.__setattr__(self, "duration_s", max(0.0, duration_s))
        object.__setattr__(self, "settle_s", max(0.0, settle_s))

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["action"] = self.action.to_dict()
        return data


@dataclass(frozen=True)
class SensorReadRequest:
    """Request for a vehicle sensor snapshot.

    The vehicle boundary is generic, but today's implementations only expose a
    fixed front camera. Additional sensors can be added without changing the
    decision loop shape.
    """

    output_dir: Path
    read_id: str = "sensor_read"
    requested_sensors: tuple[str, ...] = (FRONT_CAMERA_SENSOR_ID,)
    front_camera_endpoint: str = "/frame.jpg"
    image_extension: str = "jpg"

    def sensor_requested(self, sensor_id: str) -> bool:
        return sensor_id in self.requested_sensors

    def front_camera_path(self) -> Path:
        extension = self.image_extension.lstrip(".") or "jpg"
        return self.output_dir / f"{self.read_id}_{FRONT_CAMERA_SENSOR_ID}.{extension}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "output_dir": str(self.output_dir),
            "read_id": self.read_id,
            "requested_sensors": list(self.requested_sensors),
            "front_camera_endpoint": self.front_camera_endpoint,
            "image_extension": self.image_extension,
        }


@dataclass(frozen=True)
class SensorReading:
    """One sensor's output for a single sensor snapshot."""

    sensor_id: str
    sensor_kind: str
    captured_at_ms: int
    path: str | None = None
    value: Any = field(default=None, repr=False, compare=False)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sensor_id": self.sensor_id,
            "sensor_kind": self.sensor_kind,
            "captured_at_ms": self.captured_at_ms,
            "path": self.path,
            "has_value": self.value is not None,
            "metadata": deepcopy(self.metadata),
        }


@dataclass(frozen=True)
class SensorSnapshot:
    """A coherent read of the vehicle's available sensor inputs."""

    read_id: str
    readings: dict[str, SensorReading]
    started_at_ms: int
    completed_at_ms: int
    request: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "read_id": self.read_id,
            "started_at_ms": self.started_at_ms,
            "completed_at_ms": self.completed_at_ms,
            "request": deepcopy(self.request),
            "metadata": deepcopy(self.metadata),
            "readings": {
                sensor_id: reading.to_dict()
                for sensor_id, reading in self.readings.items()
            },
        }


@dataclass(frozen=True)
class VehicleCapabilities:
    vehicle_id: str
    vehicle_kind: str
    can_reverse: bool = True
    can_capture_frame: bool = True
    can_capture_highres: bool = True
    steering_range: tuple[float, float] = (-1.0, 1.0)
    throttle_range: tuple[float, float] = (0.0, 1.0)
    sensors: dict[str, Any] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@runtime_checkable
class CarInterface(Protocol):
    """Minimal black-box car boundary for autonomy code."""

    @property
    def capabilities(self) -> VehicleCapabilities:
        ...

    def stop(self) -> None:
        ...

    def execute_action(
        self,
        action: VehicleAction,
        *,
        throttle: float,
        recording: bool = False,
    ) -> dict[str, Any]:
        ...

    def execute_pulse(self, pulse: VehiclePulse) -> dict[str, Any]:
        ...

    def read_sensors(self, request: SensorReadRequest) -> SensorSnapshot:
        ...

VEHICLE_ACTION_FIELDS = ("forward", "reverse", "steering")
