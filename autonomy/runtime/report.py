"""One frame report for every vehicle the viewer shows.

Every named field is required. Detail that only one vehicle has goes in
``values``. ``report_from_host_result``
builds that document from a completed host cycle and the control application
recorded after the cycle. Each vehicle transports the document itself.

``cycle`` contains the observation, memory, proposal, plan, and action records.
Observation, memory, and plan may be null when their steps produce no record.
All five keys stay required. Proposal and action stay objects.
``decode_records`` reconstructs those three decision records and checks that
they describe this frame.

``values["timing"]`` is the cycle's ``timing`` record from
``autonomy.decision_cycle.timing``: its total and each step's and plugin's
duration. A report from a host that does not record it has no such key.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any, Mapping

from autonomy.decision_cycle.action.result import ActionResult
from autonomy.decision_cycle.action_identifiers import require_ascii_id, require_safe_int
from autonomy.decision_cycle.cycle import CYCLE_RECORD_KEYS, export_cycle_body
from autonomy.decision_cycle.plan.values import ActionPlan
from autonomy.decision_cycle.proposal.result import ProposalResult
from autonomy.runtime.execution import require_mode
from autonomy.serialization import canonical_json_utf8, json_ready, require_canonical_export

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
    }
)
CYCLE_EXACT_KEYS = frozenset(CYCLE_RECORD_KEYS)
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
    """One cycle record. Observation, memory, and plan may be null."""

    if key in {"observation", "memory", "plan"} and record is None:
        return None
    if type(record) is not dict:
        expected = "an object or null" if key in {"observation", "memory", "plan"} else "an object"
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
    schema: str = REPORT_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != REPORT_SCHEMA:
            raise ValueError(f"schema must be {REPORT_SCHEMA!r}")
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
        if not isinstance(self.cycle, Mapping):
            raise ValueError("cycle must be an object")
        _exact_keys(self.cycle, CYCLE_EXACT_KEYS, field="cycle")
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
            raise ValueError("values must be an object")
        values = _json_copy(dict(self.values), field="values")
        if type(values) is not dict:
            raise ValueError("values must be an object")
        object.__setattr__(self, "values", values)

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
        }

    @classmethod
    def from_dict(cls, payload: object) -> "HostReport":
        if not isinstance(payload, Mapping):
            raise ValueError("report must be an object")
        _exact_keys(payload, REPORT_EXACT_KEYS, field="report")
        return cls(
            schema=payload["schema"],
            host_name=payload["host_name"],
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

    def decode_records(self) -> "DecodedDecision":
        """Typed proposal, plan, and action, aligned to this frame."""

        decoded = decode_decision_records(self.cycle)
        align_decision_records(
            frame_id=self.frame_id,
            timestamp_ms=self.timestamp_ms,
            proposal=decoded.proposal,
            plan=decoded.plan,
            action=decoded.action,
        )
        return decoded


@dataclass(frozen=True)
class DecodedDecision:
    """The proposal, plan, and action records of one decoded cycle."""

    proposal: ProposalResult
    plan: ActionPlan | None
    action: ActionResult


def decode_decision_records(cycle: Mapping[str, Any]) -> DecodedDecision:
    """Reconstruct the decision records and require a lossless export."""

    proposal_payload = cycle.get("proposal")
    action_payload = cycle.get("action")
    if not isinstance(proposal_payload, dict) or not isinstance(action_payload, dict):
        raise ValueError("cycle.proposal and cycle.action must be objects.")
    proposal = ProposalResult.decode(proposal_payload)
    action = ActionResult.decode(action_payload)
    plan_payload = cycle.get("plan")
    plan = None if plan_payload is None else ActionPlan.decode(plan_payload)
    exported = {
        "proposal": proposal.to_dict(),
        "plan": plan.to_dict() if plan is not None else None,
        "action": action.to_dict(),
    }
    require_canonical_export(
        {key: cycle[key] for key in exported}, exported, field="cycle"
    )
    return DecodedDecision(proposal=proposal, plan=plan, action=action)


def align_decision_records(
    *,
    frame_id: str,
    timestamp_ms: int,
    proposal: ProposalResult,
    plan: ActionPlan | None,
    action: ActionResult,
) -> None:
    """Frame, candidate, timing, and authority alignment across the three records."""

    cycle_frame_id = action.frame_id
    if frame_id != cycle_frame_id:
        raise ValueError("stream frame_id must equal cycle.frame_id.")
    if action.authority.frame_id != cycle_frame_id:
        raise ValueError("authority.frame_id must equal cycle.frame_id.")
    if proposal.frame_id != cycle_frame_id:
        raise ValueError("proposal.frame_id must equal cycle.frame_id.")
    if proposal.status != "ok" and action.status != "error":
        raise ValueError("A failed proposal step must end the action with status error.")
    if plan is not None and sorted(item.proposal_id for item in plan.candidates) != sorted(
        item.proposal_id for item in proposal.candidates
    ):
        raise ValueError("plan candidates must be the proposal step's candidates.")
    if plan is not None and plan.frame_id != cycle_frame_id:
        raise ValueError("plan.frame_id must equal cycle.frame_id.")
    if plan is not None and plan.timestamp_ms != timestamp_ms:
        raise ValueError("plan.timestamp_ms must equal the cycle timestamp_ms.")

    proposed = action.authority.proposed
    if action.status == "error" or plan is None or plan.status == "idle":
        if proposed is not None:
            raise ValueError(
                "authority.proposed must be null when plan is idle/absent or the cycle failed."
            )
        return
    mismatch = "authority.proposed must equal the plan command."
    command = None if plan is None else plan.command
    if proposed is None or command is None:
        raise ValueError(mismatch)
    try:
        same = canonical_json_utf8(json_ready(proposed.to_dict())) == canonical_json_utf8(
            json_ready(command.to_dict())
        )
    except ValueError as exc:
        raise ValueError(
            f"authority.proposed/plan command are not strictly JSON-serializable: {exc}"
        ) from exc
    if not same:
        raise ValueError(mismatch)


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


def report_from_host_result(
    result: Any,
    *,
    host_name: str,
    run_id: str,
    generation_id: str,
    published_at_ms: int | None = None,
    values: Mapping[str, Any] | None = None,
) -> HostReport:
    """The viewer document for one completed host cycle.

    ``result`` is the host's cycle result: its frame context, proposal, plan,
    and action, plus the ``ControlApplication`` recorded after the cycle.
    The application is the host's write, not the action step's earlier
    authority envelope. The result's ``timing``, when it has one, goes in
    ``values["timing"]``.
    """

    context = getattr(result, "context", None)
    proposal = getattr(result, "proposal", None)
    action = getattr(result, "action", None)
    application = getattr(result, "application", None)
    if context is None or proposal is None or action is None:
        raise TypeError("host result must include a frame, proposal, and action")
    if application is None or getattr(application, "control", None) is None:
        raise TypeError("host result must include its control application")
    control = application.control
    values = {} if values is None else dict(values)
    timing = getattr(result, "timing", None)
    if timing is not None:
        values["timing"] = timing
    export = getattr(result, "export_cycle", None)
    cycle = export() if callable(export) else export_cycle_body(result)
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
        application=ReportApplication(
            applied=application.applied,
            mode=application.mode,
            reason=application.reason,
            steering=control.steering,
            throttle=control.throttle,
        ),
        values=values,
    )
