"""The CLI's reader of a host's boundary telemetry.

``implementations/runtime/picar/host_telemetry.py`` produces the records and
owns their limits, modes and reasons. These checks hold each record the CLI
reads to that contract and join it to the decision publication it reports on.
"""

from __future__ import annotations

import json
import math
from copy import deepcopy
from functools import partial

from autonomy.decision_cycle.action_identifiers import (
    require_ascii_id,
    require_safe_int,
)
from autonomy.runtime.report import HostReport
from autonomy.runtime.routes import TELEMETRY_SCHEMA
from implementations.runtime.picar.host_telemetry import (
    HOST_TELEMETRY_LIMITS,
    HOST_TELEMETRY_MODES,
    HOST_TELEMETRY_REASONS,
)
from .publication_fields import PublicationError, require_id, require_mapping

HOST_TELEMETRY_PANEL_SCHEMA = "automa_host_boundary_telemetry_panel"
HOST_TELEMETRY_NORMALIZED_SCHEMA = "automa_host_boundary_telemetry_normalized"


class HostTelemetryError(PublicationError):
    """A host-boundary telemetry publication fails its consumer contract."""


def _host_telemetry_error(
    reason: str,
    message: str,
    *,
    field: str | None = None,
    details: dict[str, object] | None = None,
) -> HostTelemetryError:
    if reason not in HOST_TELEMETRY_REASONS:
        reason = "field_invalid"
    error_details = dict(details or {})
    if field is not None:
        error_details["field"] = field
    return HostTelemetryError(reason, message, details=error_details)


def _telemetry_field_error(text: str, field: str) -> HostTelemetryError:
    return _host_telemetry_error("field_invalid", f"Host telemetry {text}", field=field)


_host_require_mapping = partial(require_mapping, invalid=_telemetry_field_error)
_host_require_id = partial(require_id, invalid=_telemetry_field_error)


def _host_require_epoch_ms(value: object, *, field: str) -> int:
    try:
        return require_safe_int(value, field_name=field)
    except ValueError as exc:
        raise _host_telemetry_error(
            "field_invalid",
            f"Host telemetry {field} must be a non-negative epoch-ms integer.",
            field=field,
        ) from exc


def _host_require_nonnegative_int(value: object, *, field: str) -> int:
    try:
        return require_safe_int(value, field_name=field)
    except ValueError as exc:
        raise _host_telemetry_error(
            "field_invalid",
            f"Host telemetry {field} must be a non-negative integer.",
            field=field,
        ) from exc


def _host_require_number(value: object, *, field: str) -> float:
    if type(value) not in {int, float} or not math.isfinite(float(value)):
        raise _host_telemetry_error(
            "field_invalid",
            f"Host telemetry {field} must be a finite non-bool number.",
            field=field,
        )
    normalized = float(value)
    if not -1.0 <= normalized <= 1.0:
        raise _host_telemetry_error(
            "field_invalid",
            f"Host telemetry {field} must be in the normalized [-1.0, 1.0] range.",
            field=field,
        )
    return normalized


def _host_require_command(value: object, *, field: str) -> dict[str, float]:
    command = _host_require_mapping(value, field=field)
    return {
        "steering": _host_require_number(command.get("steering"), field=f"{field}.steering"),
        "throttle": _host_require_number(command.get("throttle"), field=f"{field}.throttle"),
    }


def _host_require_limits(value: object) -> dict[str, int]:
    limits = _host_require_mapping(value, field="limits")
    normalized: dict[str, int] = {}
    for key, expected in HOST_TELEMETRY_LIMITS.items():
        actual = _host_require_nonnegative_int(limits.get(key), field=f"limits.{key}")
        if actual != expected:
            raise _host_telemetry_error(
                "field_invalid",
                f"Host telemetry limits.{key} must remain {expected} in this frontier.",
                field=f"limits.{key}",
            )
        normalized[key] = actual
    return normalized


def _host_strict_copy(value: object, *, field: str) -> object:
    try:
        return json.loads(
            json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True)
        )
    except (TypeError, ValueError) as exc:
        raise _host_telemetry_error(
            "field_invalid",
            f"Host telemetry {field} is not strict JSON.",
            field=field,
        ) from exc


def _host_status_error(publication: dict[str, object]) -> None:
    status = publication.get("status")
    if status == "healthy":
        return
    if status == "warming":
        raise _host_telemetry_error(
            "warming",
            "Host telemetry producer has not published a complete first record.",
        )
    if status == "stopped":
        raise _host_telemetry_error(
            "producer_stopped",
            "Host telemetry producer has stopped.",
        )
    if status == "stale":
        declared = publication.get("reason")
        reason = (
            "source_stale"
            if declared in {"source_stale", "stale_source", "source"}
            else "publication_stale"
        )
        raise _host_telemetry_error(
            reason,
            f"Host telemetry producer reported {reason}.",
        )
    if status == "mismatched":
        raise _host_telemetry_error(
            "identity_mismatch",
            "Host telemetry producer reported an identity mismatch.",
        )
    if status == "unavailable":
        reason = publication.get("reason")
        if reason not in HOST_TELEMETRY_REASONS:
            reason = "publisher_missing"
        raise _host_telemetry_error(
            str(reason),
            f"Host telemetry producer is unavailable: {reason}.",
        )
    if status == "error":
        reason = publication.get("reason")
        if reason not in HOST_TELEMETRY_REASONS:
            reason = "observer_error"
        raise _host_telemetry_error(
            str(reason),
            f"Host telemetry producer reported an error: {reason}.",
        )
    raise _host_telemetry_error(
        "field_invalid",
        "Host telemetry latest status is invalid.",
        field="status",
    )


def _host_panel_status(reason: str) -> str:
    if reason in {"source_stale", "publication_stale", "future_dated"}:
        return "stale"
    if reason == "producer_stopped":
        return "stopped"
    if reason == "warming":
        return "warming"
    if reason == "identity_mismatch":
        return "mismatched"
    if reason in {"sequence_gap", "coverage_gap", "sequence_regressed", "sequence_duplicate"}:
        return "limited"
    if reason == "publisher_missing":
        return "unavailable"
    return "error"


def host_telemetry_failure(
    reason: str,
    *,
    message: str | None = None,
    details: dict[str, object] | None = None,
) -> dict[str, object]:
    """Build an explicit empty host-telemetry panel without inventing values."""

    if reason not in HOST_TELEMETRY_REASONS:
        reason = "field_invalid"
    return {
        "schema": HOST_TELEMETRY_PANEL_SCHEMA,
        "status": _host_panel_status(reason),
        "reason": reason,
        "message": message or reason,
        "joined": False,
        "identity": None,
        "source_frame": None,
        "host_tick": None,
        "mode": None,
        "user_input": None,
        "pilot_output": None,
        "host_selected_output": None,
        "application": None,
        "limits": None,
        "freshness": None,
        "record": None,
        "details": deepcopy(details or {}),
    }


def _host_identity_from_record(
    *,
    host_name: str,
    source_id: str,
    run_id: str,
    generation_id: str,
    source_frame: dict[str, object],
) -> dict[str, object]:
    return {
        "host_name": host_name,
        "source_id": source_id,
        "run_id": run_id,
        "generation_id": generation_id,
        "source_frame": {
            "frame_id": source_frame["frame_id"],
            "frame_index": source_frame["frame_index"],
            "captured_at_ms": source_frame["captured_at_ms"],
            "completed_at_ms": source_frame["completed_at_ms"],
        },
    }


def _checked_now(now_ms: int) -> None:
    if type(now_ms) is not int or now_ms < 0:
        raise ValueError("now_ms must be a non-negative non-bool int")


def _checked_host_name(host_name: str | None) -> str | None:
    if host_name is None:
        return None
    try:
        return require_ascii_id(host_name, field_name="host_name")
    except ValueError as exc:
        raise ValueError("host_name must be a valid identity") from exc


def _telemetry_mapping(record: object) -> dict[str, object]:
    if not isinstance(record, dict):
        raise _host_telemetry_error(
            "schema_invalid",
            "Host telemetry latest publication must be an object.",
        )
    if record.get("schema") != TELEMETRY_SCHEMA:
        raise _host_telemetry_error(
            "schema_invalid",
            f"Host telemetry schema must be {TELEMETRY_SCHEMA!r}.",
            field="schema",
        )
    _host_status_error(record)
    required = {
        "host_name",
        "source_id",
        "run_id",
        "generation_id",
        "source_frame",
        "host_tick",
        "mode",
        "user_input",
        "pilot_output",
        "host_selected_output",
        "application",
        "limits",
    }
    missing = sorted(required - set(record))
    if missing:
        raise _host_telemetry_error(
            "field_invalid",
            f"Host telemetry record is missing required fields: {', '.join(missing)}.",
            details={"missing": missing},
        )
    return record


def _record_identity(record: dict[str, object], *, host_name: str | None) -> dict[str, str]:
    record_host_name = _host_require_id(record.get("host_name"), field="host_name")
    if host_name is not None and record_host_name != host_name:
        raise _host_telemetry_error(
            "identity_mismatch",
            "Host telemetry host_name does not match the requested host.",
            field="host_name",
        )
    return {
        "host_name": record_host_name,
        "source_id": _host_require_id(record.get("source_id"), field="source_id"),
        "run_id": _host_require_id(record.get("run_id"), field="run_id"),
        "generation_id": _host_require_id(record.get("generation_id"), field="generation_id"),
    }


def _record_source_frame(record: dict[str, object]) -> dict[str, object]:
    source_frame = _host_require_mapping(record.get("source_frame"), field="source_frame")
    required = {"frame_id", "frame_index", "captured_at_ms", "completed_at_ms"}
    missing = sorted(required - set(source_frame))
    if missing:
        raise _host_telemetry_error(
            "field_invalid",
            "Host telemetry source_frame is missing required fields.",
            field="source_frame",
            details={"missing": missing},
        )
    captured_at_ms = _host_require_epoch_ms(
        source_frame.get("captured_at_ms"), field="source_frame.captured_at_ms"
    )
    completed_at_ms = _host_require_epoch_ms(
        source_frame.get("completed_at_ms"), field="source_frame.completed_at_ms"
    )
    if captured_at_ms > completed_at_ms:
        raise _host_telemetry_error(
            "field_invalid",
            "Host telemetry source_frame captured_at_ms cannot exceed completed_at_ms.",
            field="source_frame",
        )
    return {
        "frame_id": _host_require_id(source_frame.get("frame_id"), field="source_frame.frame_id"),
        "frame_index": _host_require_nonnegative_int(
            source_frame.get("frame_index"), field="source_frame.frame_index"
        ),
        "captured_at_ms": captured_at_ms,
        "completed_at_ms": completed_at_ms,
    }


def _record_host_tick(record: dict[str, object]) -> dict[str, object]:
    host_tick = _host_require_mapping(record.get("host_tick"), field="host_tick")
    required = {
        "sequence",
        "observed_at_ms",
        "published_at_ms",
        "source_age_ms",
        "gap_since_previous_ms",
        "skipped_since_previous",
    }
    missing = sorted(required - set(host_tick))
    if missing:
        raise _host_telemetry_error(
            "field_invalid",
            "Host telemetry host_tick is missing required fields.",
            field="host_tick",
            details={"missing": missing},
        )
    sequence = _host_require_nonnegative_int(host_tick.get("sequence"), field="host_tick.sequence")
    if sequence < 1:
        raise _host_telemetry_error(
            "field_invalid",
            "Host telemetry host_tick.sequence must start at 1.",
            field="host_tick.sequence",
        )
    gap_value = host_tick.get("gap_since_previous_ms")
    skipped_value = host_tick.get("skipped_since_previous")
    if sequence == 1:
        if gap_value is not None or skipped_value is not None:
            raise _host_telemetry_error(
                "field_invalid",
                "The first host telemetry record must have null gap/skipped fields.",
                field="host_tick",
            )
        gap_since_previous_ms: int | None = None
        skipped_since_previous: int | None = None
    else:
        gap_since_previous_ms = _host_require_nonnegative_int(
            gap_value, field="host_tick.gap_since_previous_ms"
        )
        skipped_since_previous = _host_require_nonnegative_int(
            skipped_value, field="host_tick.skipped_since_previous"
        )
    return {
        "sequence": sequence,
        "observed_at_ms": _host_require_epoch_ms(
            host_tick.get("observed_at_ms"), field="host_tick.observed_at_ms"
        ),
        "published_at_ms": _host_require_epoch_ms(
            host_tick.get("published_at_ms"), field="host_tick.published_at_ms"
        ),
        "source_age_ms": _host_require_nonnegative_int(
            host_tick.get("source_age_ms"), field="host_tick.source_age_ms"
        ),
        "gap_since_previous_ms": gap_since_previous_ms,
        "skipped_since_previous": skipped_since_previous,
    }


def _record_boundary(record: dict[str, object]) -> dict[str, object]:
    mode = record.get("mode")
    if type(mode) is not str or mode not in HOST_TELEMETRY_MODES:
        raise _host_telemetry_error(
            "field_invalid",
            "Host telemetry mode is not one of user, local_angle, or local.",
            field="mode",
        )
    application = _host_require_mapping(record.get("application"), field="application")
    if application.get("boundary") != "post_drive_mode_pre_drivetrain":
        raise _host_telemetry_error(
            "field_invalid",
            "Host telemetry application.boundary is invalid.",
            field="application.boundary",
        )
    if application.get("actuator_feedback") != "unavailable":
        raise _host_telemetry_error(
            "field_invalid",
            "Host telemetry actuator feedback must remain unavailable.",
            field="application.actuator_feedback",
        )
    return {
        "mode": mode,
        "user_input": _host_require_command(record.get("user_input"), field="user_input"),
        "pilot_output": _host_require_command(record.get("pilot_output"), field="pilot_output"),
        "host_selected_output": _host_require_command(
            record.get("host_selected_output"), field="host_selected_output"
        ),
    }


def _record_freshness(
    *,
    now_ms: int,
    completed_at_ms: int,
    host_tick: dict[str, object],
    limits: dict[str, int],
) -> dict[str, int]:
    observed_at_ms = host_tick["observed_at_ms"]
    published_at_ms = host_tick["published_at_ms"]
    source_age_ms = host_tick["source_age_ms"]
    computed_source_age_ms = observed_at_ms - completed_at_ms
    if computed_source_age_ms < 0:
        raise _host_telemetry_error(
            "future_dated",
            "Host telemetry source completion is after host observation.",
            field="source_frame.completed_at_ms",
        )
    if source_age_ms != computed_source_age_ms:
        raise _host_telemetry_error(
            "field_invalid",
            "Host telemetry source_age_ms does not match observed/completed timestamps.",
            field="host_tick.source_age_ms",
            details={"expected": computed_source_age_ms, "actual": source_age_ms},
        )
    if published_at_ms < observed_at_ms:
        raise _host_telemetry_error(
            "field_invalid",
            "Host telemetry published_at_ms cannot precede observed_at_ms.",
            field="host_tick.published_at_ms",
        )
    future_skew_ms = max(0, published_at_ms - now_ms)
    if observed_at_ms > now_ms + limits["future_skew_tolerance_ms"]:
        raise _host_telemetry_error(
            "future_dated",
            "Host telemetry observation is outside the permitted future skew.",
            field="host_tick.observed_at_ms",
            details={"future_skew_ms": observed_at_ms - now_ms},
        )
    if published_at_ms > now_ms + limits["future_skew_tolerance_ms"]:
        raise _host_telemetry_error(
            "future_dated",
            "Host telemetry publication is outside the permitted future skew.",
            field="host_tick.published_at_ms",
            details={"future_skew_ms": future_skew_ms},
        )
    publication_age_ms = now_ms - published_at_ms
    if source_age_ms > limits["max_source_age_ms"]:
        raise _host_telemetry_error(
            "source_stale",
            f"Host telemetry source age {source_age_ms} ms exceeds its limit.",
            field="host_tick.source_age_ms",
        )
    if publication_age_ms > limits["max_publication_age_ms"]:
        raise _host_telemetry_error(
            "publication_stale",
            f"Host telemetry publication age {publication_age_ms} ms exceeds its limit.",
            field="host_tick.published_at_ms",
        )
    return {
        "observed_at_ms": observed_at_ms,
        "published_at_ms": published_at_ms,
        "source_age_ms": source_age_ms,
        "publication_age_ms": publication_age_ms,
        "future_skew_ms": future_skew_ms,
        "max_source_age_ms": limits["max_source_age_ms"],
        "max_publication_age_ms": limits["max_publication_age_ms"],
        "max_gap_ms": limits["max_gap_ms"],
        "future_skew_tolerance_ms": limits["future_skew_tolerance_ms"],
    }


def normalize_host_telemetry_record(
    record: object,
    *,
    now_ms: int,
    host_name: str | None = None,
) -> dict[str, object]:
    """Validate one latest host telemetry point.

    Sequence, gap, and skipped values remain producer facts on the point.
    """

    _checked_now(now_ms)
    host_name = _checked_host_name(host_name)
    body = _telemetry_mapping(record)
    named = _record_identity(body, host_name=host_name)
    source_frame = _record_source_frame(body)
    host_tick = _record_host_tick(body)
    boundary = _record_boundary(body)
    limits = _host_require_limits(body.get("limits"))
    freshness = _record_freshness(
        now_ms=now_ms,
        completed_at_ms=source_frame["completed_at_ms"],
        host_tick=host_tick,
        limits=limits,
    )
    identity = _host_identity_from_record(
        host_name=named["host_name"],
        source_id=named["source_id"],
        run_id=named["run_id"],
        generation_id=named["generation_id"],
        source_frame=source_frame,
    )
    return {
        "schema": HOST_TELEMETRY_NORMALIZED_SCHEMA,
        "status": "healthy",
        "reason": "",
        "record": _host_strict_copy(body, field="record"),
        "identity": identity,
        "source_frame": deepcopy(identity["source_frame"]),
        "host_tick": host_tick,
        "mode": boundary["mode"],
        "user_input": boundary["user_input"],
        "pilot_output": boundary["pilot_output"],
        "host_selected_output": boundary["host_selected_output"],
        "application": {
            "boundary": "post_drive_mode_pre_drivetrain",
            "actuator_feedback": "unavailable",
        },
        "limits": limits,
        "freshness": freshness,
    }


def _decision_source_frame(report: HostReport) -> dict[str, object]:
    """The report's own frame: its id, index, capture, and completion."""

    try:
        frame = {
            "frame_id": report.frame_id,
            "frame_index": _host_require_nonnegative_int(
                report.frame_index, field="decision.frame_index"
            ),
            "captured_at_ms": _host_require_epoch_ms(
                report.timestamp_ms, field="decision.timestamp_ms"
            ),
            "completed_at_ms": _host_require_epoch_ms(
                report.published_at_ms, field="decision.published_at_ms"
            ),
        }
    except HostTelemetryError as exc:
        raise _host_telemetry_error(
            "identity_mismatch",
            "Decision source-frame identity is invalid.",
            details=exc.details,
        ) from exc
    if frame["captured_at_ms"] > frame["completed_at_ms"]:
        raise _host_telemetry_error(
            "identity_mismatch",
            "Decision source-frame timestamps are invalid.",
        )
    return frame


def decision_publication_identity(normalized_decision: dict[str, object]) -> dict[str, object]:
    """Extract the exact composite identity needed for a telemetry join."""

    report = normalized_decision.get("report")
    if not isinstance(report, HostReport):
        raise _host_telemetry_error(
            "identity_mismatch",
            "Decision publication has no decision report.",
        )
    values = report.values
    try:
        source_id = _host_require_id(values.get("source_id"), field="decision.values.source_id")
        source_frame = _decision_source_frame(report)
    except HostTelemetryError as exc:
        if exc.reason == "identity_mismatch":
            raise
        raise _host_telemetry_error(
            "identity_mismatch",
            "Decision publication cannot expose exact telemetry identity.",
            details=exc.details,
        ) from exc
    return {
        "host_name": report.host_name,
        "source_id": source_id,
        "run_id": report.run_id,
        "generation_id": report.generation_id,
        "source_frame": source_frame,
    }


def _host_telemetry_panel_from_normalized(
    normalized: dict[str, object],
    *,
    joined: bool,
) -> dict[str, object]:
    return {
        "schema": HOST_TELEMETRY_PANEL_SCHEMA,
        "status": normalized.get("status"),
        "reason": normalized.get("reason", ""),
        "message": normalized.get("reason", "") or "",
        "joined": joined,
        "identity": deepcopy(normalized.get("identity")),
        "source_frame": deepcopy(normalized.get("source_frame")),
        "host_tick": deepcopy(normalized.get("host_tick")),
        "mode": normalized.get("mode"),
        "user_input": deepcopy(normalized.get("user_input")),
        "pilot_output": deepcopy(normalized.get("pilot_output")),
        "host_selected_output": deepcopy(normalized.get("host_selected_output")),
        "application": deepcopy(normalized.get("application")),
        "limits": deepcopy(normalized.get("limits")),
        "freshness": deepcopy(normalized.get("freshness")),
        "record": deepcopy(normalized.get("record")),
        "details": {},
    }


def join_host_telemetry_to_decision(
    telemetry: object,
    normalized_decision: dict[str, object],
    *,
    now_ms: int | None = None,
    host_name: str | None = None,
) -> dict[str, object]:
    """Join a normalized/latest telemetry point to a decision exactly."""

    if isinstance(telemetry, dict) and telemetry.get("schema") == TELEMETRY_SCHEMA:
        if now_ms is None:
            raise ValueError("now_ms is required when joining a raw telemetry record")
        telemetry = normalize_host_telemetry_record(
            telemetry,
            now_ms=now_ms,
            host_name=host_name,
        )
    if not isinstance(telemetry, dict) or telemetry.get("schema") != HOST_TELEMETRY_NORMALIZED_SCHEMA:
        raise _host_telemetry_error(
            "schema_invalid",
            "Host telemetry join requires a normalized telemetry point.",
        )
    if telemetry.get("status") != "healthy":
        reason = telemetry.get("reason")
        raise _host_telemetry_error(
            reason if reason in HOST_TELEMETRY_REASONS else "field_invalid",
            "Host telemetry point is not healthy enough to join.",
        )
    decision_identity = decision_publication_identity(normalized_decision)
    telemetry_identity = telemetry.get("identity")
    if not isinstance(telemetry_identity, dict) or telemetry_identity != decision_identity:
        raise _host_telemetry_error(
            "identity_mismatch",
            "Host telemetry identity does not exactly match the decision.",
            details={
                "decision_identity": decision_identity,
                "telemetry_identity": deepcopy(telemetry_identity),
            },
        )
    panel = _host_telemetry_panel_from_normalized(telemetry, joined=True)
    panel["decision"] = {
        "frame_id": decision_identity["source_frame"]["frame_id"],
        "frame_index": decision_identity["source_frame"]["frame_index"],
    }
    return panel
