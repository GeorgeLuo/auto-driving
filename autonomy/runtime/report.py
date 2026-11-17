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

``values.timing`` is the cycle's ``timing`` record from
``autonomy.decision_cycle.timing``: its total and each step's and plugin's
duration. A host that does not record it writes ``null``.
``values.activation.steps`` is the step-activation payloads.
``source_id`` is an ascii id. ``source_id_for`` writes
``{runtime}:{host_name}`` when that fits, and the host name when a
64-character name leaves no room for the prefix. The telemetry join
compares this id with the telemetry record's source id.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from typing import Any

from autonomy.decision_cycle.action.result import ActionResult
from autonomy.decision_cycle.memory.interface import MemoryReport
from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.perception.interface import PerceptionText
from autonomy.decision_cycle.plan.values import ActionPlan
from autonomy.decision_cycle.proposal.result import ProposalResult
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
ACTIVATION_KEYS = frozenset({"steps"})
VALUES_KEYS = frozenset({
    "source_id",
    "activation",
    "stale_after_ms",
    "delivered_at_ms",
    "delivery_error",
    "receipt",
    "timing",
})
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


def _object(value: object, *, field_name: str) -> dict[str, object]:
    copied = _json_copy(value, field=field_name)
    if type(copied) is not dict:
        raise FieldError(field_name, "must be an object")
    return copied


def _object_or_null(value: object, *, field_name: str) -> dict[str, object] | None:
    if value is None:
        return None
    return _object(value, field_name=field_name)


@dataclass(frozen=True)
class ReportCycle:
    """The six typed records of a completed or partial cycle."""

    perception: PerceptionText | None = None
    observation: Observation | None = None
    memory: MemoryReport | None = None
    proposal: ProposalResult | None = None
    plan: ActionPlan | None = None
    action: ActionResult | None = None
    # Local reports reuse the result's serialization cache only when writing JSON.
    # replace() clears this reference when any typed record changes.
    _result: DecisionCycleResult | None = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        for name, record_type in (("perception", PerceptionText), ("observation", Observation), ("memory", MemoryReport),
                                  ("proposal", ProposalResult), ("plan", ActionPlan), ("action", ActionResult)):
            record = getattr(self, name)
            if record is not None and not isinstance(record, record_type):
                raise FieldError(name, f"must be {record_type.__name__} or null")

    def record(self, step: str) -> PerceptionText | Observation | MemoryReport | ProposalResult | ActionPlan | ActionResult | None:
        """The typed record for a named step stream."""
        require_choice(step, CYCLE_RECORD_KEYS, field_name="step")
        return getattr(self, step)

    def to_dict(self) -> dict[str, object]:
        if self._result is not None:
            return _json_copy(self._result.export_cycle(), field="cycle")
        return _json_copy({
            name: None if self.record(name) is None else self.record(name).to_dict()
            for name in CYCLE_RECORD_KEYS
        }, field="cycle")

    @classmethod
    def from_result(cls, result: DecisionCycleResult) -> ReportCycle:
        cycle = cls(
            perception=result.perception, observation=result.observation, memory=result.memory,
            proposal=result.proposal, plan=result.plan, action=result.action,
        )
        object.__setattr__(cycle, "_result", result)
        return cycle

    @classmethod
    def from_dict(cls, payload: object) -> ReportCycle:
        document = require_keys(payload, CYCLE_EXACT_KEYS)
        parsed = {}
        for name, parse in (("perception", PerceptionText.from_dict), ("observation", Observation.from_dict),
                            ("memory", MemoryReport.from_dict), ("proposal", ProposalResult.from_dict),
                            ("plan", ActionPlan.from_dict), ("action", ActionResult.from_dict)):
            record = document[name]
            if record == {}:
                raise FieldError(name, "must be a nonempty step record or null")
            parsed[name] = None if record is None else require_nested(parse, record, field_name=name)
        return cls(**parsed)


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
class ReportActivation:
    """The selection published on one frame. ``steps`` stays the activation payloads."""

    steps: dict[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "steps", _object(self.steps, field_name="steps"))

    def to_dict(self) -> dict[str, object]:
        return {"steps": _json_copy(self.steps, field="steps")}

    @classmethod
    def from_dict(cls, payload: object) -> "ReportActivation":
        document = require_keys(payload, ACTIVATION_KEYS)
        return cls(steps=document["steps"])


@dataclass(frozen=True)
class ReportValues:
    """Vehicle detail on one frame. Nullable fields are written as ``null``."""

    source_id: str
    activation: ReportActivation
    stale_after_ms: int
    delivered_at_ms: int | None = None
    delivery_error: str | None = None
    receipt: dict[str, object] | None = None
    timing: dict[str, object] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_id", require_ascii_id(self.source_id, field_name="source_id"))
        ceiling = require_safe_int(self.stale_after_ms, field_name="stale_after_ms")
        if ceiling == 0:
            raise FieldError("stale_after_ms", "must be positive")
        object.__setattr__(self, "stale_after_ms", ceiling)
        if self.delivered_at_ms is not None:
            object.__setattr__(
                self, "delivered_at_ms",
                require_safe_int(self.delivered_at_ms, field_name="delivered_at_ms"),
            )
        if self.delivery_error is not None:
            object.__setattr__(
                self, "delivery_error", require_text(self.delivery_error, field_name="delivery_error"),
            )
        object.__setattr__(self, "receipt", _object_or_null(self.receipt, field_name="receipt"))
        object.__setattr__(self, "timing", _object_or_null(self.timing, field_name="timing"))

    def to_dict(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "activation": self.activation.to_dict(),
            "stale_after_ms": self.stale_after_ms,
            "delivered_at_ms": self.delivered_at_ms,
            "delivery_error": self.delivery_error,
            "receipt": None if self.receipt is None else _json_copy(self.receipt, field="receipt"),
            "timing": None if self.timing is None else _json_copy(self.timing, field="timing"),
        }

    @classmethod
    def from_dict(cls, payload: object) -> "ReportValues":
        document = require_keys(payload, VALUES_KEYS)
        return cls(
            source_id=document["source_id"],
            activation=require_nested(
                ReportActivation.from_dict, document["activation"], field_name="activation",
            ),
            stale_after_ms=document["stale_after_ms"],
            delivered_at_ms=document["delivered_at_ms"],
            delivery_error=document["delivery_error"],
            receipt=document["receipt"],
            timing=document["timing"],
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
    cycle: ReportCycle
    application: ReportApplication
    values: ReportValues
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
        if not isinstance(self.cycle, ReportCycle):  # pyright: ignore[reportUnnecessaryIsInstance] - validate runtime callers
            raise TypeError("cycle must be ReportCycle")
        if not isinstance(self.application, ReportApplication):
            raise TypeError("application must be ReportApplication")
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
            "cycle": self.cycle.to_dict(),
            "application": self.application.to_dict(),
            "values": self.values.to_dict(),
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
            cycle=require_nested(ReportCycle.from_dict, document["cycle"], field_name="cycle"),
            application=require_nested(
                ReportApplication.from_dict, document["application"], field_name="application",
            ),
            values=require_nested(ReportValues.from_dict, document["values"], field_name="values"),
            status=document["status"],
            error=document["error"],
        )


def source_id_for(runtime: str, host_name: str) -> str:
    """An ascii id for one runtime's frames on this host.

    ``{runtime}:{host_name}`` is the id when it matches. A host name of 64
    characters leaves no room for the runtime prefix, and that name is already
    an id, so it is the source id.
    """

    prefixed = f"{runtime}:{host_name}"
    try:
        return require_ascii_id(prefixed, field_name="source_id")
    except FieldError:
        return require_ascii_id(host_name, field_name="host_name")


def _safe_int_or_null(value: object, *, field_name: str) -> int | None:
    if value is None:
        return None
    try:
        return require_safe_int(value, field_name=field_name)
    except FieldError:
        return None


def _text_or_null(value: object, *, field_name: str) -> str | None:
    if value is None:
        return None
    try:
        return require_text(value, field_name=field_name)
    except FieldError:
        return None


def delivery_values(application: object) -> tuple[int | None, str | None, dict[str, object] | None]:
    """Delivery detail that is not one of the named application fields.

    Both vehicles put this on ``ReportValues``. A value that is not the
    field's kind is written as ``null`` so the named application still publishes.
    """

    delivered_at_ms = _safe_int_or_null(
        getattr(application, "delivered_at_ms", None), field_name="delivered_at_ms",
    )
    delivery_error = _text_or_null(getattr(application, "error", None), field_name="delivery_error")
    receipt = getattr(application, "receipt", None)
    copied: dict[str, object] | None = None
    if type(receipt) is dict:
        try:
            parsed = _json_copy(receipt, field="receipt")
        except FieldError:
            parsed = None
        if type(parsed) is dict:
            copied = parsed
    return delivered_at_ms, delivery_error, copied


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
    values: ReportValues,
    status: str = "ok",
    error: str | None = None,
) -> HostReport:
    """The viewer document for one completed host cycle.

    ``result`` is the host's cycle result. Any step may be null. The
    application is the host's write, including the idle write a raised cycle
    made, not the action step's earlier authority envelope. The result's
    ``timing`` goes on ``values.timing``, as ``null`` when the cycle has none.
    ``status`` is ``ok`` unless execution raised.
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
    values = replace(values, timing=result.timing)
    cycle = ReportCycle.from_result(result)
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
