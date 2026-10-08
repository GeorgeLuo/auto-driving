"""One frame report for every vehicle the viewer shows.

``vehicle_report_v0`` replaces ``vehicle_decision_stream_frame_v0`` and
``provider_decision_stream_frame_v0``. Every named field is required. Detail
that only one vehicle has goes in ``values``. ``report_from_host_result``
builds that document from a completed host cycle and the control application
recorded after the cycle. Each vehicle transports the document itself.

``cycle`` is the proposal, plan, and action records. ``plan`` is null when
planning did not produce a record, which is how a failed cycle is represented.
The key stays required. Proposal and action stay objects, so the existing
typed cycle check can still reconstruct them.
"""

from __future__ import annotations

import math
import time
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
    """A detached JSON value, including tuple arrays from typed cycle exports."""

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
    if type(value) in (list, tuple):
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


def _cycle_record(record: object, *, key: str) -> Any:
    """One decision record. ``plan`` may be null; the other two stay objects."""

    if key == "plan" and record is None:
        return None
    if type(record) is not dict:
        expected = "an object or null" if key == "plan" else "an object"
        raise ValueError(f"cycle.{key} must be {expected}")
    return record


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
            key: _cycle_record(
                _json_copy(self.cycle[key], field=f"cycle.{key}"),
                key=key,
            )
            for key in ("proposal", "plan", "action")
        }
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


def delivery_values(application: Any) -> dict[str, Any]:
    """Delivery detail that is not one of the named application fields.

    Both vehicles put this under ``values``. A receipt that is not JSON is
    omitted so the named application still publishes.
    """

    values: dict[str, Any] = {}
    delivered_at_ms = getattr(application, "delivered_at_ms", None)
    if type(delivered_at_ms) is int:
        values["delivered_at_ms"] = delivered_at_ms
    error = getattr(application, "error", None)
    if type(error) is str and error:
        values["delivery_error"] = error
    receipt = getattr(application, "receipt", None)
    if type(receipt) is dict:
        try:
            copied = _json_copy(receipt, field="values.receipt")
        except ValueError:
            copied = None
        if type(copied) is dict:
            values["receipt"] = copied
    return values


def diagnostic_ceiling(values: Mapping[str, Any] | None) -> int:
    """Display age limit published on the report, in milliseconds.

    Both vehicles write ``values.stale_after_ms`` and both viewers honor it.
    Command expiry stays in control execution and is not this number.
    """

    raw = None if values is None else values.get("stale_after_ms")
    if type(raw) is not int or raw <= 0:
        raise ValueError("values.stale_after_ms must be a positive int")
    return raw


def _export_record(record: Any, *, field: str) -> dict[str, Any]:
    export = record.to_dict() if callable(getattr(record, "to_dict", None)) else record
    if not isinstance(export, Mapping):
        raise TypeError(f"{field} must be an object")
    return dict(export)


def report_from_host_result(
    result: Any,
    *,
    vehicle_id: str,
    run_id: str,
    generation_id: str,
    published_at_ms: int | None = None,
    values: Mapping[str, Any] | None = None,
) -> VehicleReport:
    """The viewer document for one completed host cycle.

    ``result`` is the host's cycle result: its frame context, proposal, plan,
    and action, plus the ``ControlApplication`` recorded after the cycle.
    The application is the host's write, not the action step's earlier
    authority envelope.
    """

    context = getattr(result, "context", None)
    proposal = getattr(result, "proposal", None)
    action = getattr(result, "action", None)
    application = getattr(result, "application", None)
    if context is None or proposal is None or action is None:
        raise TypeError("host result must include a frame, proposal, and action")
    if application is None or getattr(application, "control", None) is None:
        raise TypeError("host result must include its control application")
    plan = getattr(result, "plan", None)
    control = application.control
    return VehicleReport(
        vehicle_id=vehicle_id,
        run_id=run_id,
        generation_id=generation_id,
        frame_id=context.frame_id,
        frame_index=context.frame_index,
        timestamp_ms=context.timestamp_ms,
        published_at_ms=(
            int(time.time() * 1000) if published_at_ms is None else int(published_at_ms)
        ),
        cycle={
            "proposal": _export_record(proposal, field="cycle.proposal"),
            "plan": None if plan is None else _export_record(plan, field="cycle.plan"),
            "action": _export_record(action, field="cycle.action"),
        },
        application=ReportApplication(
            applied=application.applied,
            mode=application.mode,
            reason=application.reason,
            steering=control.steering,
            throttle=control.throttle,
        ),
        values={} if values is None else dict(values),
    )
