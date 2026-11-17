"""One frame report for every vehicle the viewer shows.

Every named field is required. Detail that only one vehicle has goes in
``values``. ``report_from_host_result``
builds that document from a completed host cycle and the control application
recorded after the cycle. Each vehicle transports the document itself.

``cycle`` contains the perception, observation, memory, proposal, plan, and
action records. Any step may be null. ``status`` is ``ok`` when the cycle
returned and ``error`` when it raised. ``error`` is null on success and a
nonempty string when execution raised. A non-ok action record is not a frame
error.

``values["timing"]`` is the cycle's ``timing`` record from
``autonomy.decision_cycle.timing``: its total and each step's and plugin's
duration. A report from a host that does not record it has no such key.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Mapping

from autonomy.decision_cycle.cycle import CYCLE_RECORD_KEYS, DecisionCycleResult
from autonomy.fields import (
    FieldError,
    require_ascii_id,
    require_choice,
    require_keys,
    require_nested,
    require_number,
    require_safe_int,
    require_schema,
    require_text,
)
from autonomy.runtime.execution import require_mode

REPORT_SCHEMA = "host_report"
REPORT_EXACT_KEYS = frozenset(
    {
        "schema",
        "host_name",
        "run_id",
        "generation_id",
        "frame_id",
        "frame_index",
        "timestamp_ms",
        "published_at_ms",
        "cycle",
        "application",
        "values",
        "status",
        "error",
    }
)
FRAME_STATUSES = frozenset({"ok", "error"})
CYCLE_EXACT_KEYS = frozenset(CYCLE_RECORD_KEYS)
APPLICATION_EXACT_KEYS = frozenset({"applied", "mode", "reason", "steering", "throttle"})
UNIT = (-1.0, 1.0)


def _json_copy(value: object, *, field: str) -> Any:
    """A detached JSON value, including tuple arrays from typed cycle exports."""

    if value is None or type(value) is str:
        return value
    if type(value) is bool:
        return value
    if type(value) is int:
        return value
    if type(value) is float:
        return require_number(value, field_name=field)
    if type(value) in (list, tuple):
        return [_json_copy(item, field=field) for item in value]
    if type(value) is dict:
        copied: dict[str, Any] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise FieldError(field, "keys must be strings")
            copied[key] = _json_copy(item, field=f"{field}.{key}")
        return copied
    raise FieldError(field, f"must be JSON data; got {type(value).__name__}")


def _cycle_record(record: object, *, key: str) -> Any:
    """One cycle record. Every step may be null."""

    if record is None:
        return None
    if type(record) is not dict:
        raise FieldError(f"cycle.{key}", "must be an object or null")
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
            raise FieldError("applied", "must be a bool")
        object.__setattr__(self, "mode", require_mode(self.mode))
        object.__setattr__(self, "reason", require_text(self.reason, field_name="reason"))
        object.__setattr__(self, "steering", require_number(self.steering, field_name="steering", bounds=UNIT))
        object.__setattr__(self, "throttle", require_number(self.throttle, field_name="throttle", bounds=UNIT))

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
        document = require_keys(payload, APPLICATION_EXACT_KEYS)
        return cls(
            applied=document["applied"],
            mode=document["mode"],
            reason=document["reason"],
            steering=document["steering"],
            throttle=document["throttle"],
        )


@dataclass(frozen=True)
class HostReport:
    """The frame document both vehicles give the viewer."""

    host_name: str
    run_id: str
    generation_id: str
    frame_id: str
    frame_index: int
    timestamp_ms: int
    published_at_ms: int
    cycle: dict[str, Any]
    application: ReportApplication
    values: dict[str, Any]
    status: str = "ok"
    error: str | None = None
    schema: str = REPORT_SCHEMA

    def __post_init__(self) -> None:
        require_choice(self.schema, (REPORT_SCHEMA,), field_name="schema")
        object.__setattr__(
            self, "host_name", require_ascii_id(self.host_name, field_name="host_name")
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
        require_keys(self.cycle, CYCLE_EXACT_KEYS, field_name="cycle")
        cycle = {
            key: _cycle_record(
                _json_copy(self.cycle[key], field=f"cycle.{key}"),
                key=key,
            )
            for key in CYCLE_RECORD_KEYS
        }
        object.__setattr__(self, "cycle", cycle)
        if not isinstance(self.application, ReportApplication):
            raise TypeError("application must be ReportApplication")
        if not isinstance(self.values, Mapping):
            raise FieldError("values", "must be an object")
        values = _json_copy(dict(self.values), field="values")
        if type(values) is not dict:
            raise FieldError("values", "must be an object")
        object.__setattr__(self, "values", values)
        require_choice(self.status, FRAME_STATUSES, field_name="status")
        if self.status == "ok":
            if self.error is not None:
                raise FieldError("error", "must be null when status is ok")
        else:
            require_text(self.error, field_name="error")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "host_name": self.host_name,
            "run_id": self.run_id,
            "generation_id": self.generation_id,
            "frame_id": self.frame_id,
            "frame_index": self.frame_index,
            "timestamp_ms": self.timestamp_ms,
            "published_at_ms": self.published_at_ms,
            "cycle": _json_copy(self.cycle, field="cycle"),
            "application": self.application.to_dict(),
            "values": _json_copy(self.values, field="values"),
            "status": self.status,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, payload: object) -> "HostReport":
        document = require_schema(payload, REPORT_SCHEMA, REPORT_EXACT_KEYS)
        return cls(
            schema=document["schema"],
            host_name=document["host_name"],
            run_id=document["run_id"],
            generation_id=document["generation_id"],
            frame_id=document["frame_id"],
            frame_index=document["frame_index"],
            timestamp_ms=document["timestamp_ms"],
            published_at_ms=document["published_at_ms"],
            cycle=document["cycle"],
            application=require_nested(ReportApplication.from_dict, document["application"], field_name="application"),
            values=document["values"],
            status=document["status"],
            error=document["error"],
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


def error_application(mode: str) -> ReportApplication:
    """The idle application published when a cycle raises."""

    from autonomy.runtime.execution import MODES

    return ReportApplication(
        applied=False,
        mode=mode if mode in MODES else "manual",
        reason="observation-cycle-error",
        steering=0.0,
        throttle=0.0,
    )


def report_from_host_result(
    result: DecisionCycleResult,
    *,
    host_name: str,
    run_id: str,
    generation_id: str,
    published_at_ms: int | None = None,
    values: Mapping[str, Any] | None = None,
    status: str = "ok",
    error: str | None = None,
) -> HostReport:
    """The viewer document for one completed host cycle.

    ``result`` is the host's cycle result. Any step may be null. The
    application is the host's write, including the idle write a raised cycle
    made, not the action step's earlier authority envelope. The result's
    ``timing``, when it has one, goes in ``values["timing"]``. ``status`` is
    ``ok`` unless execution raised.
    """

    context = result.context
    application = result.application
    if application is None or application.control is None:
        raise TypeError("host result must include its control application")
    control = application.control
    report_application = ReportApplication(
        applied=application.applied,
        mode=application.mode,
        reason=application.reason,
        steering=control.steering,
        throttle=control.throttle,
    )
    values = {} if values is None else dict(values)
    if result.timing is not None:
        values["timing"] = result.timing
    cycle = result.export_cycle()
    return HostReport(
        host_name=host_name,
        run_id=run_id,
        generation_id=generation_id,
        frame_id=context.frame_id,
        frame_index=context.frame_index,
        timestamp_ms=context.timestamp_ms,
        published_at_ms=(
            int(time.time() * 1000) if published_at_ms is None else int(published_at_ms)
        ),
        cycle=cycle,
        application=report_application,
        values=values,
        status=status,
        error=error,
    )
