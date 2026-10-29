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
from typing import Any

from autonomy.decision_cycle.action_identifiers import (
    require_ascii_id,
    require_safe_int,
)
from autonomy.runtime.routes import TELEMETRY_SCHEMA
from implementations.runtime.picar.host_telemetry import (
    HOST_TELEMETRY_LIMITS,
    HOST_TELEMETRY_MODES,
    HOST_TELEMETRY_REASONS,
)
from ..shared.publication_fields import PublicationError, require_id, require_mapping

HOST_TELEMETRY_RECORDS_SCHEMA = "automa_host_boundary_telemetry_records"
HOST_TELEMETRY_PANEL_SCHEMA = "automa_host_boundary_telemetry_panel"
HOST_TELEMETRY_NORMALIZED_SCHEMA = "automa_host_boundary_telemetry_normalized"
HOST_TELEMETRY_RECORDS_NORMALIZED_SCHEMA = "automa_host_boundary_telemetry_records_normalized"


class HostTelemetryError(PublicationError):
    """A host-boundary telemetry publication fails its consumer contract."""


def _host_telemetry_error(
    reason: str,
    message: str,
    *,
    field: str | None = None,
    details: dict[str, Any] | None = None,
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


def _host_strict_copy(value: object, *, field: str) -> Any:
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


def _host_status_error(publication: dict[str, Any], *, records: bool = False) -> None:
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
    route = "records" if records else "latest"
    raise _host_telemetry_error(
        "field_invalid",
        f"Host telemetry {route} status is invalid.",
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
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
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
        "coverage": {
            "status": "unavailable",
            "reason": reason,
            "interval_covered": False,
            "record_count": 0,
        },
        "record": None,
        "details": deepcopy(details or {}),
    }


def _host_identity_from_record(
    *,
    host_name: str,
    source_id: str,
    run_id: str,
    generation_id: str,
    activation: dict[str, Any],
    source_frame: dict[str, Any],
) -> dict[str, Any]:
    return {
        "host_name": host_name,
        "source_id": source_id,
        "run_id": run_id,
        "generation_id": generation_id,
        "activation": {
            "generation_id": activation["generation_id"],
        },
        "source_frame": {
            "frame_id": source_frame["frame_id"],
            "frame_index": source_frame["frame_index"],
            "captured_at_ms": source_frame["captured_at_ms"],
            "completed_at_ms": source_frame["completed_at_ms"],
        },
    }


def normalize_host_telemetry_record(
    record: object,
    *,
    now_ms: int,
    host_name: str | None = None,
    previous_record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize one producer record and enforce the amended consumer contract.

    The latest route is intentionally point-oriented.  It never turns the
    latest point into interval coverage; callers use the records route for
    contiguous capture claims.
    """

    if type(now_ms) is not int or now_ms < 0:
        raise ValueError("now_ms must be a non-negative non-bool int")
    if host_name is not None:
        try:
            host_name = require_ascii_id(host_name, field_name="host_name")
        except ValueError as exc:
            raise ValueError("host_name must be a valid identity") from exc
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
        "activation",
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

    record_host_name = _host_require_id(record.get("host_name"), field="host_name")
    if host_name is not None and record_host_name != host_name:
        raise _host_telemetry_error(
            "identity_mismatch",
            "Host telemetry host_name does not match the requested host.",
            field="host_name",
        )
    source_id = _host_require_id(record.get("source_id"), field="source_id")
    run_id = _host_require_id(record.get("run_id"), field="run_id")
    generation_id = _host_require_id(record.get("generation_id"), field="generation_id")

    activation = _host_require_mapping(record.get("activation"), field="activation")
    activation_required = {"generation_id"}
    missing_activation = sorted(activation_required - set(activation))
    if missing_activation:
        raise _host_telemetry_error(
            "field_invalid",
            "Host telemetry activation is missing required fields.",
            field="activation",
            details={"missing": missing_activation},
        )
    activation_generation_id = _host_require_id(
        activation.get("generation_id"), field="activation.generation_id"
    )
    if activation_generation_id != generation_id:
        raise _host_telemetry_error(
            "field_invalid",
            "Host telemetry activation.generation_id must equal generation_id.",
            field="activation.generation_id",
        )

    source_frame = _host_require_mapping(record.get("source_frame"), field="source_frame")
    source_frame_required = {
        "frame_id",
        "frame_index",
        "captured_at_ms",
        "completed_at_ms",
    }
    missing_source_frame = sorted(source_frame_required - set(source_frame))
    if missing_source_frame:
        raise _host_telemetry_error(
            "field_invalid",
            "Host telemetry source_frame is missing required fields.",
            field="source_frame",
            details={"missing": missing_source_frame},
        )
    source_frame_id = _host_require_id(source_frame.get("frame_id"), field="source_frame.frame_id")
    source_frame_index = _host_require_nonnegative_int(
        source_frame.get("frame_index"), field="source_frame.frame_index"
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

    host_tick = _host_require_mapping(record.get("host_tick"), field="host_tick")
    tick_required = {
        "sequence",
        "observed_at_ms",
        "published_at_ms",
        "source_age_ms",
        "gap_since_previous_ms",
        "skipped_since_previous",
    }
    missing_tick = sorted(tick_required - set(host_tick))
    if missing_tick:
        raise _host_telemetry_error(
            "field_invalid",
            "Host telemetry host_tick is missing required fields.",
            field="host_tick",
            details={"missing": missing_tick},
        )
    sequence = _host_require_nonnegative_int(host_tick.get("sequence"), field="host_tick.sequence")
    if sequence < 1:
        raise _host_telemetry_error(
            "field_invalid",
            "Host telemetry host_tick.sequence must start at 1.",
            field="host_tick.sequence",
        )
    observed_at_ms = _host_require_epoch_ms(
        host_tick.get("observed_at_ms"), field="host_tick.observed_at_ms"
    )
    published_at_ms = _host_require_epoch_ms(
        host_tick.get("published_at_ms"), field="host_tick.published_at_ms"
    )
    source_age_ms = _host_require_nonnegative_int(
        host_tick.get("source_age_ms"), field="host_tick.source_age_ms"
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

    mode = record.get("mode")
    if type(mode) is not str or mode not in HOST_TELEMETRY_MODES:
        raise _host_telemetry_error(
            "field_invalid",
            "Host telemetry mode is not one of user, local_angle, or local.",
            field="mode",
        )
    user_input = _host_require_command(record.get("user_input"), field="user_input")
    pilot_output = _host_require_command(record.get("pilot_output"), field="pilot_output")
    host_selected_output = _host_require_command(
        record.get("host_selected_output"), field="host_selected_output"
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
    limits = _host_require_limits(record.get("limits"))

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

    previous_tick: dict[str, Any] | None = None
    if previous_record is not None:
        if "host_tick" in previous_record and isinstance(previous_record.get("host_tick"), dict):
            previous_tick = previous_record["host_tick"]
        elif "record" in previous_record and isinstance(previous_record.get("record"), dict):
            previous_tick = previous_record["record"].get("host_tick")
    coverage_status = "point"
    coverage_reason = "coverage_gap"
    interval_covered = False
    if previous_tick is not None:
        previous_sequence = previous_tick.get("sequence")
        previous_observed_at_ms = previous_tick.get("observed_at_ms")
        if type(previous_sequence) is not int or type(previous_observed_at_ms) is not int:
            raise _host_telemetry_error(
                "field_invalid",
                "Previous host telemetry record has invalid ordering fields.",
            )
        if sequence < previous_sequence:
            raise _host_telemetry_error(
                "sequence_regressed",
                "Host telemetry sequence regressed.",
                field="host_tick.sequence",
            )
        if sequence == previous_sequence:
            raise _host_telemetry_error(
                "sequence_duplicate",
                "Host telemetry sequence was duplicated.",
                field="host_tick.sequence",
            )
        expected_skipped = sequence - previous_sequence - 1
        expected_gap = observed_at_ms - previous_observed_at_ms
        if expected_gap < 0:
            raise _host_telemetry_error(
                "sequence_regressed",
                "Host telemetry observation clock regressed.",
                field="host_tick.observed_at_ms",
            )
        if gap_since_previous_ms != expected_gap or skipped_since_previous != expected_skipped:
            raise _host_telemetry_error(
                "field_invalid",
                "Host telemetry gap/skipped fields do not match adjacent records.",
                field="host_tick",
                details={
                    "expected_gap_since_previous_ms": expected_gap,
                    "actual_gap_since_previous_ms": gap_since_previous_ms,
                    "expected_skipped_since_previous": expected_skipped,
                    "actual_skipped_since_previous": skipped_since_previous,
                },
            )
        if expected_skipped > 0 or expected_gap > limits["max_gap_ms"]:
            coverage_status = "limited"
            coverage_reason = "sequence_gap"
        else:
            coverage_status = "contiguous"
            coverage_reason = ""
            interval_covered = True
    elif sequence > 1:
        # A latest point can still be consumed, but it cannot prove the
        # interval preceding it.  The producer-reported gap is retained in
        # the point model and the records route is required for coverage.
        if (skipped_since_previous or 0) > 0 or (
            gap_since_previous_ms is not None
            and gap_since_previous_ms > limits["max_gap_ms"]
        ):
            coverage_status = "limited"
            coverage_reason = "sequence_gap"

    detached_record = _host_strict_copy(record, field="record")
    normalized_host_tick = {
        "sequence": sequence,
        "observed_at_ms": observed_at_ms,
        "published_at_ms": published_at_ms,
        "source_age_ms": source_age_ms,
        "gap_since_previous_ms": gap_since_previous_ms,
        "skipped_since_previous": skipped_since_previous,
    }
    identity = _host_identity_from_record(
        host_name=record_host_name,
        source_id=source_id,
        run_id=run_id,
        generation_id=generation_id,
        activation={
            "generation_id": activation_generation_id,
        },
        source_frame={
            "frame_id": source_frame_id,
            "frame_index": source_frame_index,
            "captured_at_ms": captured_at_ms,
            "completed_at_ms": completed_at_ms,
        },
    )
    return {
        "schema": HOST_TELEMETRY_NORMALIZED_SCHEMA,
        "status": "limited" if coverage_status == "limited" else "healthy",
        "reason": coverage_reason if coverage_status == "limited" else "",
        "record": detached_record,
        "identity": identity,
        "source_frame": deepcopy(identity["source_frame"]),
        "host_tick": normalized_host_tick,
        "mode": mode,
        "user_input": user_input,
        "pilot_output": pilot_output,
        "host_selected_output": host_selected_output,
        "application": {
            "boundary": "post_drive_mode_pre_drivetrain",
            "actuator_feedback": "unavailable",
        },
        "limits": limits,
        "freshness": {
            "observed_at_ms": observed_at_ms,
            "published_at_ms": published_at_ms,
            "source_age_ms": source_age_ms,
            "publication_age_ms": publication_age_ms,
            "future_skew_ms": future_skew_ms,
            "max_source_age_ms": limits["max_source_age_ms"],
            "max_publication_age_ms": limits["max_publication_age_ms"],
            "max_gap_ms": limits["max_gap_ms"],
            "future_skew_tolerance_ms": limits["future_skew_tolerance_ms"],
        },
        "coverage": {
            "status": coverage_status,
            "reason": coverage_reason,
            "interval_covered": interval_covered,
            "sequence": sequence,
            "record_count": 1,
        },
    }


def _host_records_result(
    *,
    status: str,
    reason: str,
    records: list[dict[str, Any]],
    after_sequence: int,
    limit: int,
    coverage: dict[str, Any],
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema": HOST_TELEMETRY_RECORDS_NORMALIZED_SCHEMA,
        "status": status,
        "reason": reason,
        "after_sequence": after_sequence,
        "limit": limit,
        "records": records,
        "coverage": coverage,
        "details": deepcopy(details or {}),
    }


def normalize_host_telemetry_records(
    publication: object,
    *,
    now_ms: int,
    after_sequence: int = 0,
    limit: int = 128,
    host_name: str | None = None,
) -> dict[str, Any]:
    """Normalize bounded history and report interval coverage explicitly."""

    if type(now_ms) is not int or now_ms < 0:
        raise ValueError("now_ms must be a non-negative non-bool int")
    if type(after_sequence) is not int or after_sequence < 0:
        raise ValueError("after_sequence must be a non-negative integer")
    if type(limit) is not int or not 1 <= limit <= 128:
        raise ValueError("limit must be an integer from 1 through 128")
    if not isinstance(publication, dict):
        raise _host_telemetry_error(
            "schema_invalid",
            "Host telemetry records publication must be an object.",
        )
    schema = publication.get("schema")
    if schema not in {HOST_TELEMETRY_RECORDS_SCHEMA, TELEMETRY_SCHEMA}:
        raise _host_telemetry_error(
            "schema_invalid",
            "Host telemetry records schema is invalid.",
            field="schema",
        )
    try:
        _host_status_error(publication, records=True)
    except HostTelemetryError as exc:
        return _host_records_result(
            status=_host_panel_status(exc.reason),
            reason=exc.reason,
            records=[],
            after_sequence=after_sequence,
            limit=limit,
            coverage={
                "status": "unavailable",
                "reason": exc.reason,
                "interval_covered": False,
                "record_count": 0,
            },
            details=exc.details,
        )

    raw_records = publication.get("records")
    if not isinstance(raw_records, list):
        raise _host_telemetry_error(
            "field_invalid",
            "Host telemetry records.records must be an array.",
            field="records",
        )
    if len(raw_records) > limit:
        raise _host_telemetry_error(
            "field_invalid",
            "Host telemetry records response exceeds the requested limit.",
            field="records",
            details={"count": len(raw_records), "limit": limit},
        )
    coverage_payload = publication.get("coverage")
    coverage_payload = coverage_payload if isinstance(coverage_payload, dict) else {}
    coverage_reason = publication.get("coverage_reason")
    if not isinstance(coverage_reason, str) or not coverage_reason:
        coverage_reason = coverage_payload.get("reason")
    if not isinstance(coverage_reason, str) or not coverage_reason:
        coverage_reason = ""
    history_evicted = coverage_reason == "history_evicted"
    explicit_baseline = bool(
        coverage_payload.get("baseline") is True
        or coverage_payload.get("explicit_baseline") is True
    )
    complete_declared = coverage_payload.get("complete")
    normalized_records: list[dict[str, Any]] = []
    previous: dict[str, Any] | None = None
    failure: HostTelemetryError | None = None
    for index, raw_record in enumerate(raw_records):
        try:
            normalized = normalize_host_telemetry_record(
                raw_record,
                now_ms=now_ms,
                host_name=host_name,
                previous_record=previous,
            )
            if previous is not None and normalized["identity"] != {
                **previous["identity"],
                "source_frame": normalized["identity"]["source_frame"],
            }:
                # Only the source-frame component may change across a valid
                # history sequence.  A runtime/session change closes coverage.
                if any(
                    normalized["identity"].get(key) != previous["identity"].get(key)
                    for key in ("host_name", "source_id", "run_id", "generation_id", "activation")
                ):
                    raise _host_telemetry_error(
                        "identity_mismatch",
                        "Host telemetry history changed runtime identity.",
                        field=f"records[{index}].identity",
                    )
            normalized_records.append(normalized)
            previous = normalized
            if normalized["coverage"]["status"] == "limited":
                failure = _host_telemetry_error(
                    "sequence_gap",
                    "Host telemetry history contains an uncovered sequence interval.",
                    field=f"records[{index}].host_tick",
                )
                break
        except HostTelemetryError as exc:
            failure = exc
            break

    if failure is not None:
        return _host_records_result(
            status=_host_panel_status(failure.reason),
            reason=failure.reason,
            records=normalized_records,
            after_sequence=after_sequence,
            limit=limit,
            coverage={
                "status": "limited",
                "reason": failure.reason,
                "coverage_reason": coverage_reason or failure.reason,
                "interval_covered": False,
                "record_count": len(normalized_records),
                "first_sequence": (
                    normalized_records[0]["host_tick"]["sequence"]
                    if normalized_records
                    else None
                ),
                "last_sequence": (
                    normalized_records[-1]["host_tick"]["sequence"]
                    if normalized_records
                    else None
                ),
            },
            details=failure.details,
        )

    if not normalized_records:
        return _host_records_result(
            status="limited",
            reason="coverage_gap",
            records=[],
            after_sequence=after_sequence,
            limit=limit,
            coverage={
                "status": "limited",
                "reason": "coverage_gap",
                "coverage_reason": coverage_reason or "empty_interval",
                "interval_covered": False,
                "record_count": 0,
            },
        )

    first_sequence = normalized_records[0]["host_tick"]["sequence"]
    last_sequence = normalized_records[-1]["host_tick"]["sequence"]
    if first_sequence != after_sequence + 1 and not explicit_baseline:
        return _host_records_result(
            status="limited",
            reason="coverage_gap",
            records=normalized_records,
            after_sequence=after_sequence,
            limit=limit,
            coverage={
                "status": "limited",
                "reason": "coverage_gap",
                "coverage_reason": coverage_reason or "missing_baseline",
                "interval_covered": False,
                "record_count": len(normalized_records),
                "first_sequence": first_sequence,
                "last_sequence": last_sequence,
            },
        )

    interval_covered = all(
        item["coverage"]["status"] == "contiguous" for item in normalized_records[1:]
    )
    if not explicit_baseline and first_sequence == 1:
        # Sequence 1 is a valid explicit baseline.  It does not claim a
        # covered interval before itself, but the returned interval is valid.
        interval_covered = interval_covered and True
    if history_evicted or complete_declared is False:
        interval_covered = False
    if not interval_covered:
        reason = "coverage_gap"
        return _host_records_result(
            status="limited",
            reason=reason,
            records=normalized_records,
            after_sequence=after_sequence,
            limit=limit,
            coverage={
                "status": "limited",
                "reason": reason,
                "coverage_reason": coverage_reason or "incomplete",
                "interval_covered": False,
                "record_count": len(normalized_records),
                "first_sequence": first_sequence,
                "last_sequence": last_sequence,
            },
        )
    return _host_records_result(
        status="healthy",
        reason="",
        records=normalized_records,
        after_sequence=after_sequence,
        limit=limit,
        coverage={
            "status": "complete",
            "reason": "",
            "coverage_reason": coverage_reason,
            "interval_covered": True,
            "record_count": len(normalized_records),
            "first_sequence": first_sequence,
            "last_sequence": last_sequence,
        },
    )


def _decision_source_frame(decision: dict[str, Any]) -> dict[str, Any]:
    values = decision.get("values") if isinstance(decision.get("values"), dict) else {}
    candidates = (
        values.get("source_frame"),
        decision.get("source_frame"),
        decision.get("frame"),
    )
    required = {"frame_id", "frame_index", "captured_at_ms", "completed_at_ms"}
    candidate: object
    for candidate in candidates:
        if isinstance(candidate, dict) and required.issubset(candidate):
            try:
                frame = {
                    "frame_id": _host_require_id(candidate.get("frame_id"), field="decision.source_frame.frame_id"),
                    "frame_index": _host_require_nonnegative_int(
                        candidate.get("frame_index"), field="decision.source_frame.frame_index"
                    ),
                    "captured_at_ms": _host_require_epoch_ms(
                        candidate.get("captured_at_ms"), field="decision.source_frame.captured_at_ms"
                    ),
                    "completed_at_ms": _host_require_epoch_ms(
                        candidate.get("completed_at_ms"), field="decision.source_frame.completed_at_ms"
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
    raise _host_telemetry_error(
        "identity_mismatch",
        "Decision publication cannot expose the complete source-frame identity.",
        field="decision.source_frame",
    )


def decision_publication_identity(normalized_decision: dict[str, Any]) -> dict[str, Any]:
    """Extract the exact composite identity needed for a telemetry join."""

    decision = normalized_decision.get("decision")
    if not isinstance(decision, dict):
        raise _host_telemetry_error(
            "identity_mismatch",
            "Decision publication has no decision object.",
        )
    values = decision.get("values") if isinstance(decision.get("values"), dict) else {}
    activation = values.get("activation") if isinstance(values.get("activation"), dict) else None
    if not isinstance(activation, dict):
        raise _host_telemetry_error(
            "identity_mismatch",
            "Decision publication cannot expose activation identity.",
        )
    try:
        host_name = _host_require_id(decision.get("host_name"), field="decision.host_name")
        source_id = _host_require_id(values.get("source_id"), field="decision.values.source_id")
        run_id = _host_require_id(decision.get("run_id"), field="decision.run_id")
        generation_id = _host_require_id(
            decision.get("generation_id"), field="decision.generation_id"
        )
        activation_generation_id = _host_require_id(
            activation.get("generation_id"), field="decision.values.activation.generation_id"
        )
        source_frame = _decision_source_frame(decision)
    except HostTelemetryError as exc:
        if exc.reason == "identity_mismatch":
            raise
        raise _host_telemetry_error(
            "identity_mismatch",
            "Decision publication cannot expose exact telemetry identity.",
            details=exc.details,
        ) from exc
    if activation_generation_id != generation_id:
        raise _host_telemetry_error(
            "identity_mismatch",
            "Decision activation generation does not match its envelope.",
        )
    if decision.get("frame_id") != source_frame["frame_id"]:
        raise _host_telemetry_error(
            "identity_mismatch",
            "Decision frame_id does not match its source-frame identity.",
        )
    if decision.get("frame_index") != source_frame["frame_index"]:
        raise _host_telemetry_error(
            "identity_mismatch",
            "Decision frame_index does not match its source-frame identity.",
        )
    if decision.get("timestamp_ms") != source_frame["captured_at_ms"]:
        raise _host_telemetry_error(
            "identity_mismatch",
            "Decision timestamp does not match its source-frame identity.",
        )
    return {
        "host_name": host_name,
        "source_id": source_id,
        "run_id": run_id,
        "generation_id": generation_id,
        "activation": {
            "generation_id": activation_generation_id,
        },
        "source_frame": source_frame,
    }


def _host_telemetry_panel_from_normalized(
    normalized: dict[str, Any],
    *,
    joined: bool,
) -> dict[str, Any]:
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
        "coverage": deepcopy(normalized.get("coverage")),
        "record": deepcopy(normalized.get("record")),
        "details": {},
    }


def join_host_telemetry_to_decision(
    telemetry: object,
    normalized_decision: dict[str, Any],
    *,
    now_ms: int | None = None,
    host_name: str | None = None,
) -> dict[str, Any]:
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
