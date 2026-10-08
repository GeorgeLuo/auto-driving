"""One frame report for every vehicle the viewer shows.

``vehicle_report_v0`` replaces ``vehicle_decision_stream_frame_v0`` and
``provider_decision_stream_frame_v0``. Every named field is required. Detail
that only one vehicle has goes in ``values``. This module defines the document.
It does not publish it or adapt either vehicle.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

from autonomy.decision_cycle.action_identifiers import require_ascii_id, require_safe_int
from autonomy.runtime.execution import require_mode

REPORT_SCHEMA = "vehicle_report_v0"
REPORT_EXACT_KEYS = frozenset(
    {
        "schema",
        "vehicle_id",
        "run_id",
        "generation_id",
        "frame_id",
        "frame_index",
        "timestamp_ms",
        "published_at_ms",
        "cycle",
        "application",
        "values",
    }
)
CYCLE_EXACT_KEYS = frozenset({"proposal", "plan", "action"})
APPLICATION_EXACT_KEYS = frozenset({"applied", "mode", "reason", "steering", "throttle"})


def _exact_keys(payload: Mapping[str, Any], expected: frozenset[str], *, field: str) -> None:
    actual = set(payload)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing or extra:
        raise ValueError(
            f"{field} keys must be {sorted(expected)}; missing={missing} extra={extra}"
        )


def _json_copy(value: object, *, field: str) -> Any:
    """A detached JSON value. Objects and arrays stay dicts and lists."""

    if value is None or type(value) is str:
        return value
    if type(value) is bool:
        return value
    if type(value) is int:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"{field} must be finite")
        return value
    if type(value) is list:
        return [_json_copy(item, field=field) for item in value]
    if type(value) is dict:
        copied: dict[str, Any] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError(f"{field} keys must be strings")
            copied[key] = _json_copy(item, field=f"{field}.{key}")
        return copied
    raise ValueError(f"{field} must be JSON data; got {type(value).__name__}")


def _unit(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite number in [-1, 1]")
    number = float(value)
    if not math.isfinite(number) or number < -1.0 or number > 1.0:
        raise ValueError(f"{field} must be a finite number in [-1, 1]")
    return number


@dataclass(frozen=True)
class ReportApplication:
    """The command written for this frame, and whether that write happened."""

    applied: bool
    mode: str
    reason: str
    steering: float
    throttle: float

    def __post_init__(self) -> None:
        if type(self.applied) is not bool:
            raise ValueError("application.applied must be a bool")
        object.__setattr__(self, "mode", require_mode(self.mode))
        if type(self.reason) is not str:
            raise ValueError("application.reason must be a string")
        object.__setattr__(self, "steering", _unit(self.steering, field="application.steering"))
        object.__setattr__(self, "throttle", _unit(self.throttle, field="application.throttle"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "applied": self.applied,
            "mode": self.mode,
            "reason": self.reason,
            "steering": self.steering,
            "throttle": self.throttle,
        }

    @classmethod
    def from_dict(cls, payload: object) -> "ReportApplication":
        if not isinstance(payload, Mapping):
            raise ValueError("application must be an object")
        _exact_keys(payload, APPLICATION_EXACT_KEYS, field="application")
        return cls(
            applied=payload["applied"],
            mode=payload["mode"],
            reason=payload["reason"],
            steering=payload["steering"],
            throttle=payload["throttle"],
        )


@dataclass(frozen=True)
class VehicleReport:
    """The frame document both vehicles give the viewer."""

    vehicle_id: str
    run_id: str
    generation_id: str
    frame_id: str
    frame_index: int
    timestamp_ms: int
    published_at_ms: int
    cycle: dict[str, Any]
    application: ReportApplication
    values: dict[str, Any]
    schema: str = REPORT_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != REPORT_SCHEMA:
            raise ValueError(f"schema must be {REPORT_SCHEMA!r}")
        object.__setattr__(
            self, "vehicle_id", require_ascii_id(self.vehicle_id, field_name="vehicle_id")
        )
        object.__setattr__(self, "run_id", require_ascii_id(self.run_id, field_name="run_id"))
        object.__setattr__(
            self, "generation_id", require_ascii_id(self.generation_id, field_name="generation_id")
        )
        object.__setattr__(self, "frame_id", require_ascii_id(self.frame_id, field_name="frame_id"))
        object.__setattr__(
            self, "frame_index", require_safe_int(self.frame_index, field_name="frame_index")
        )
        object.__setattr__(
            self, "timestamp_ms", require_safe_int(self.timestamp_ms, field_name="timestamp_ms")
        )
        object.__setattr__(
            self,
            "published_at_ms",
            require_safe_int(self.published_at_ms, field_name="published_at_ms"),
        )
        if not isinstance(self.cycle, Mapping):
            raise ValueError("cycle must be an object")
        _exact_keys(self.cycle, CYCLE_EXACT_KEYS, field="cycle")
        cycle = {
            key: _json_copy(self.cycle[key], field=f"cycle.{key}")
            for key in ("proposal", "plan", "action")
        }
        for key, record in cycle.items():
            if type(record) is not dict:
                raise ValueError(f"cycle.{key} must be an object")
        object.__setattr__(self, "cycle", cycle)
        if not isinstance(self.application, ReportApplication):
            raise TypeError("application must be ReportApplication")
        if not isinstance(self.values, Mapping):
            raise ValueError("values must be an object")
        values = _json_copy(dict(self.values), field="values")
        if type(values) is not dict:
            raise ValueError("values must be an object")
        object.__setattr__(self, "values", values)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "vehicle_id": self.vehicle_id,
            "run_id": self.run_id,
            "generation_id": self.generation_id,
            "frame_id": self.frame_id,
            "frame_index": self.frame_index,
            "timestamp_ms": self.timestamp_ms,
            "published_at_ms": self.published_at_ms,
            "cycle": _json_copy(self.cycle, field="cycle"),
            "application": self.application.to_dict(),
            "values": _json_copy(self.values, field="values"),
        }

    @classmethod
    def from_dict(cls, payload: object) -> "VehicleReport":
        if not isinstance(payload, Mapping):
            raise ValueError("report must be an object")
        _exact_keys(payload, REPORT_EXACT_KEYS, field="report")
        return cls(
            schema=payload["schema"],
            vehicle_id=payload["vehicle_id"],
            run_id=payload["run_id"],
            generation_id=payload["generation_id"],
            frame_id=payload["frame_id"],
            frame_index=payload["frame_index"],
            timestamp_ms=payload["timestamp_ms"],
            published_at_ms=payload["published_at_ms"],
            cycle=payload["cycle"],
            application=ReportApplication.from_dict(payload["application"]),
            values=payload["values"],
        )
