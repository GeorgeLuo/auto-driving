from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.request
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from autonomy.decision.shadow_ids import require_ascii_id, require_safe_int

from .paths import safe_path_part
from .perception_view import get_perception_view_status


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ROOT = Path(os.environ.get("AUTOMA_RUNTIME_ROOT", ROOT / "runtime" / "vehicles"))

LATEST_JSON_PATH = "/autonomy/observation/latest"
LATEST_FRAME_PATH = "/autonomy/observation/latest/frame.jpg"
DECISION_LATEST_PATH = "/autonomy/decision/latest"
STATUS_JSON_PATH = "/autonomy/status"
MEMORY_RESET_PATH = "/autonomy/memory/reset"
HOST_TELEMETRY_LATEST_PATH = "/autonomy/telemetry/latest"
HOST_TELEMETRY_RECORDS_PATH = "/autonomy/telemetry/records"
PHYSICAL_RUNTIME_DIRNAME = "physical_observation"
DECISION_PUBLICATION_SCHEMA = "automa_physical_decision_publication_v0"
HOST_TELEMETRY_SCHEMA = "automa_host_boundary_telemetry_v0"
HOST_TELEMETRY_RECORDS_SCHEMA = "automa_host_boundary_telemetry_records_v0"
HOST_TELEMETRY_PANEL_SCHEMA = "automa_host_boundary_telemetry_panel_v0"
HOST_TELEMETRY_CAPTURE_SCHEMA = "automa_host_boundary_telemetry_capture_v0"
HOST_TELEMETRY_NORMALIZED_SCHEMA = "automa_host_boundary_telemetry_normalized_v0"
HOST_TELEMETRY_RECORDS_NORMALIZED_SCHEMA = "automa_host_boundary_telemetry_records_normalized_v0"

HOST_TELEMETRY_LIMITS: dict[str, int] = {
    "max_source_age_ms": 1_500,
    "max_publication_age_ms": 1_500,
    "max_gap_ms": 1_000,
    "future_skew_tolerance_ms": 250,
}
HOST_TELEMETRY_MODES = frozenset({"user", "local_angle", "local"})
HOST_TELEMETRY_REASONS = frozenset(
    {
        "publisher_missing",
        "warming",
        "producer_stopped",
        "schema_invalid",
        "field_invalid",
        "identity_mismatch",
        "future_dated",
        "source_stale",
        "publication_stale",
        "sequence_regressed",
        "sequence_duplicate",
        "sequence_gap",
        "coverage_gap",
        "observer_error",
        "method_not_allowed",
        "query_invalid",
        "redirect_rejected",
    }
)


class PhysicalDecisionPublicationError(ValueError):
    """A physical decision publication is absent or fails normalization."""

    def __init__(
        self,
        reason: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.message_text = message
        self.details = details or {}


class HostTelemetryError(ValueError):
    """A host-boundary telemetry publication fails its consumer contract."""

    def __init__(
        self,
        reason: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.message_text = message
        self.details = details or {}


# Keep a descriptive alias available to callers that use the physical route
# terminology.  Both names carry the same stable ``reason`` contract.
PhysicalHostTelemetryError = HostTelemetryError


def physical_observation_dir(vehicle_id: str) -> Path:
    return RUNTIME_ROOT / safe_path_part(vehicle_id) / PHYSICAL_RUNTIME_DIRNAME


def physical_view_status(vehicle_id: str, *, timeout_s: float = 0.25) -> dict[str, Any]:
    """Return local loopback view status for a physical observation stream."""
    return get_perception_view_status(
        physical_observation_dir(vehicle_id),
        timeout_s=timeout_s,
    )


def fetch_autonomy_status(
    base_url: str,
    *,
    timeout_s: float = 3.0,
) -> dict[str, Any]:
    """GET /autonomy/status from a physical Donkey runtime."""

    url = f"{base_url.rstrip('/')}{STATUS_JSON_PATH}"
    try:
        with urllib.request.urlopen(url, timeout=max(0.1, float(timeout_s))) as response:
            body = response.read()
            status_code = getattr(response, "status", 200)
    except urllib.error.HTTPError as exc:
        body = exc.read() if exc.fp is not None else b""
        status_code = int(exc.code)
        if not body:
            raise ConnectionError(
                f"GET {url} failed with HTTP {status_code} and empty body"
            ) from exc
    except urllib.error.URLError as exc:
        raise ConnectionError(f"GET {url} failed: {exc.reason}") from exc
    except TimeoutError as exc:
        raise ConnectionError(f"GET {url} timed out after {timeout_s}s") from exc

    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConnectionError(f"GET {url} returned non-JSON body") from exc
    if not isinstance(payload, dict):
        raise ConnectionError(f"GET {url} returned a non-object JSON payload")
    payload.setdefault("http_status", status_code)
    return payload


def post_memory_reset(
    base_url: str,
    *,
    timeout_s: float = 3.0,
) -> dict[str, Any]:
    """POST /autonomy/memory/reset on a physical Donkey runtime."""

    url = f"{base_url.rstrip('/')}{MEMORY_RESET_PATH}"
    request = urllib.request.Request(
        url,
        data=b"{}",
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=max(0.1, float(timeout_s))) as response:
            body = response.read()
            status_code = getattr(response, "status", 200)
    except urllib.error.HTTPError as exc:
        body = exc.read() if exc.fp is not None else b""
        status_code = int(exc.code)
        if not body:
            raise ConnectionError(
                f"POST {url} failed with HTTP {status_code} and empty body"
            ) from exc
    except urllib.error.URLError as exc:
        raise ConnectionError(f"POST {url} failed: {exc.reason}") from exc
    except TimeoutError as exc:
        raise ConnectionError(f"POST {url} timed out after {timeout_s}s") from exc

    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConnectionError(f"POST {url} returned non-JSON body") from exc
    if not isinstance(payload, dict):
        raise ConnectionError(f"POST {url} returned a non-object JSON payload")
    payload.setdefault("http_status", status_code)
    return payload


def fetch_observation_publication(
    base_url: str,
    *,
    timeout_s: float = 3.0,
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}{LATEST_JSON_PATH}"
    try:
        with urllib.request.urlopen(url, timeout=max(0.1, float(timeout_s))) as response:
            body = response.read()
            status_code = getattr(response, "status", 200)
    except urllib.error.HTTPError as exc:
        body = exc.read() if exc.fp is not None else b""
        status_code = int(exc.code)
        if not body:
            raise ConnectionError(
                f"GET {url} failed with HTTP {status_code} and empty body"
            ) from exc
    except urllib.error.URLError as exc:
        raise ConnectionError(f"GET {url} failed: {exc.reason}") from exc
    except TimeoutError as exc:
        raise ConnectionError(f"GET {url} timed out after {timeout_s}s") from exc

    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConnectionError(f"GET {url} returned non-JSON body") from exc
    if not isinstance(payload, dict):
        raise ConnectionError(f"GET {url} returned a non-object JSON payload")
    payload.setdefault("http_status", status_code)
    return payload


def fetch_decision_publication(
    base_url: str,
    *,
    timeout_s: float = 3.0,
) -> dict[str, Any]:
    """GET the read-only current decision publication from a PiCar runtime."""

    url = f"{base_url.rstrip('/')}{DECISION_LATEST_PATH}"
    try:
        with urllib.request.urlopen(url, timeout=max(0.1, float(timeout_s))) as response:
            body = response.read()
            status_code = getattr(response, "status", 200)
    except urllib.error.HTTPError as exc:
        body = exc.read() if exc.fp is not None else b""
        status_code = int(exc.code)
        if not body:
            raise ConnectionError(
                f"GET {url} failed with HTTP {status_code} and empty body"
            ) from exc
    except urllib.error.URLError as exc:
        raise ConnectionError(f"GET {url} failed: {exc.reason}") from exc
    except TimeoutError as exc:
        raise ConnectionError(f"GET {url} timed out after {timeout_s}s") from exc

    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConnectionError(f"GET {url} returned non-JSON body") from exc
    if not isinstance(payload, dict):
        raise ConnectionError(f"GET {url} returned a non-object JSON payload")
    payload.setdefault("http_status", status_code)
    return payload


class _RejectTelemetryRedirect(urllib.request.HTTPRedirectHandler):
    """Keep the telemetry client on the exact read-only route it requested."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        del req, fp, code, msg, headers, newurl
        return None


def _fetch_host_telemetry_json(
    url: str,
    *,
    timeout_s: float,
    query_route: bool,
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"Accept": "application/json"},
    )
    opener = urllib.request.build_opener(_RejectTelemetryRedirect)
    try:
        with opener.open(request, timeout=max(0.1, float(timeout_s))) as response:
            body = response.read()
            status_code = int(getattr(response, "status", 200))
    except urllib.error.HTTPError as exc:
        status_code = int(exc.code)
        if 300 <= status_code < 400:
            raise HostTelemetryError(
                "redirect_rejected",
                f"GET {url} returned a redirect that the telemetry client rejected.",
            ) from exc
        body = exc.read() if exc.fp is not None else b""
        if not body:
            reason = (
                "method_not_allowed"
                if status_code == 405
                else "query_invalid"
                if query_route and status_code == 400
                else "publisher_missing"
                if status_code in {404, 502, 503, 504}
                else "schema_invalid"
            )
            raise HostTelemetryError(
                reason,
                f"GET {url} failed with HTTP {status_code} and empty body.",
            ) from exc
    except urllib.error.URLError as exc:
        raise HostTelemetryError(
            "publisher_missing",
            f"GET {url} failed: {exc.reason}",
        ) from exc
    except TimeoutError as exc:
        raise HostTelemetryError(
            "publisher_missing",
            f"GET {url} timed out after {timeout_s}s.",
        ) from exc

    if 300 <= status_code < 400:
        raise HostTelemetryError(
            "redirect_rejected",
            f"GET {url} returned a redirect that the telemetry client rejected.",
        )
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HostTelemetryError(
            "schema_invalid",
            f"GET {url} returned a non-JSON body.",
        ) from exc
    if not isinstance(payload, dict):
        raise HostTelemetryError(
            "schema_invalid",
            f"GET {url} returned a non-object JSON payload.",
        )
    payload.setdefault("http_status", status_code)
    return payload


def fetch_host_telemetry_latest(
    base_url: str,
    *,
    timeout_s: float = 3.0,
) -> dict[str, Any]:
    """GET the latest host-boundary telemetry point without following redirects."""

    url = f"{base_url.rstrip('/')}{HOST_TELEMETRY_LATEST_PATH}"
    return _fetch_host_telemetry_json(url, timeout_s=timeout_s, query_route=False)


def fetch_host_telemetry_records(
    base_url: str,
    *,
    after_sequence: int = 0,
    limit: int = 128,
    timeout_s: float = 3.0,
) -> dict[str, Any]:
    """GET bounded host telemetry history using the exact contract query."""

    if type(after_sequence) is not int or after_sequence < 0:
        raise HostTelemetryError(
            "query_invalid",
            "after_sequence must be a non-negative integer.",
        )
    if type(limit) is not int or not 1 <= limit <= 128:
        raise HostTelemetryError(
            "query_invalid",
            "limit must be an integer from 1 through 128.",
        )
    query = urlencode({"after_sequence": after_sequence, "limit": limit})
    url = f"{base_url.rstrip('/')}{HOST_TELEMETRY_RECORDS_PATH}?{query}"
    return _fetch_host_telemetry_json(url, timeout_s=timeout_s, query_route=True)


def _physical_decision_error(
    reason: str,
    message: str,
    *,
    field: str | None = None,
) -> PhysicalDecisionPublicationError:
    details: dict[str, Any] = {}
    if field is not None:
        details["field"] = field
    return PhysicalDecisionPublicationError(reason, message, details=details)


def _physical_required_int(
    value: object,
    *,
    field: str,
    allow_negative: bool = False,
) -> int:
    try:
        number = require_safe_int(value, field_name=field)
    except ValueError as exc:
        if allow_negative and type(value) is int:
            return int(value)
        raise _physical_decision_error(
            "incomplete",
            f"Physical decision publication {field} must be a non-bool int.",
            field=field,
        ) from exc
    return number


def _physical_required_id(value: object, *, field: str) -> str:
    try:
        return require_ascii_id(value, field_name=field)
    except ValueError as exc:
        raise _physical_decision_error(
            "incomplete",
            f"Physical decision publication {field} is not a valid identity.",
            field=field,
        ) from exc


def _physical_require_mapping(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _physical_decision_error(
            "incomplete",
            f"Physical decision publication {field} must be an object.",
            field=field,
        )
    return value


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


def _host_require_mapping(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _host_telemetry_error(
            "field_invalid",
            f"Host telemetry {field} must be an object.",
            field=field,
        )
    return value


def _host_require_id(value: object, *, field: str) -> str:
    try:
        return require_ascii_id(value, field_name=field)
    except ValueError as exc:
        raise _host_telemetry_error(
            "field_invalid",
            f"Host telemetry {field} is not a valid identity.",
            field=field,
        ) from exc


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
    return _host_require_epoch_ms(value, field=field)


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
    vehicle_id: str,
    source_id: str,
    run_id: str,
    generation_id: str,
    activation: dict[str, Any],
    source_frame: dict[str, Any],
) -> dict[str, Any]:
    return {
        "vehicle_id": vehicle_id,
        "source_id": source_id,
        "run_id": run_id,
        "generation_id": generation_id,
        "activation": {
            "engine_id": activation["engine_id"],
            "activated_at_ms": activation["activated_at_ms"],
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
    vehicle_id: str | None = None,
    previous_record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize one producer record and enforce the amended consumer contract.

    The latest route is intentionally point-oriented.  It never turns the
    latest point into interval coverage; callers use the records route for
    contiguous capture claims.
    """

    if type(now_ms) is not int or now_ms < 0:
        raise ValueError("now_ms must be a non-negative non-bool int")
    if vehicle_id is not None:
        try:
            vehicle_id = require_ascii_id(vehicle_id, field_name="vehicle_id")
        except ValueError as exc:
            raise ValueError("vehicle_id must be a valid identity") from exc
    if not isinstance(record, dict):
        raise _host_telemetry_error(
            "schema_invalid",
            "Host telemetry latest publication must be an object.",
        )
    if record.get("schema") != HOST_TELEMETRY_SCHEMA:
        raise _host_telemetry_error(
            "schema_invalid",
            f"Host telemetry schema must be {HOST_TELEMETRY_SCHEMA!r}.",
            field="schema",
        )
    _host_status_error(record)

    required = {
        "vehicle_id",
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

    record_vehicle_id = _host_require_id(record.get("vehicle_id"), field="vehicle_id")
    if vehicle_id is not None and record_vehicle_id != vehicle_id:
        raise _host_telemetry_error(
            "identity_mismatch",
            "Host telemetry vehicle_id does not match the requested vehicle.",
            field="vehicle_id",
        )
    source_id = _host_require_id(record.get("source_id"), field="source_id")
    run_id = _host_require_id(record.get("run_id"), field="run_id")
    generation_id = _host_require_id(record.get("generation_id"), field="generation_id")

    activation = _host_require_mapping(record.get("activation"), field="activation")
    activation_required = {"engine_id", "activated_at_ms", "generation_id"}
    missing_activation = sorted(activation_required - set(activation))
    if missing_activation:
        raise _host_telemetry_error(
            "field_invalid",
            "Host telemetry activation is missing required fields.",
            field="activation",
            details={"missing": missing_activation},
        )
    activation_engine_id = _host_require_id(
        activation.get("engine_id"), field="activation.engine_id"
    )
    activation_at_ms = _host_require_epoch_ms(
        activation.get("activated_at_ms"), field="activation.activated_at_ms"
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
        vehicle_id=record_vehicle_id,
        source_id=source_id,
        run_id=run_id,
        generation_id=generation_id,
        activation={
            "engine_id": activation_engine_id,
            "activated_at_ms": activation_at_ms,
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
    vehicle_id: str | None = None,
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
    if schema not in {HOST_TELEMETRY_RECORDS_SCHEMA, HOST_TELEMETRY_SCHEMA}:
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
                vehicle_id=vehicle_id,
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
                    for key in ("vehicle_id", "source_id", "run_id", "generation_id", "activation")
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
    cycle = decision.get("cycle") if isinstance(decision.get("cycle"), dict) else {}
    source = cycle.get("source") if isinstance(cycle.get("source"), dict) else {}
    candidates = (
        decision.get("source_frame"),
        cycle.get("source_frame"),
        source.get("source_frame"),
        source,
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
                    "Physical decision source-frame identity is invalid.",
                    details=exc.details,
                ) from exc
            if frame["captured_at_ms"] > frame["completed_at_ms"]:
                raise _host_telemetry_error(
                    "identity_mismatch",
                    "Physical decision source-frame timestamps are invalid.",
                )
            return frame
    raise _host_telemetry_error(
        "identity_mismatch",
        "Physical decision publication cannot expose the complete source-frame identity.",
        field="decision.source_frame",
    )


def physical_decision_identity(normalized_decision: dict[str, Any]) -> dict[str, Any]:
    """Extract the exact composite identity needed for a telemetry join."""

    decision = normalized_decision.get("decision")
    if not isinstance(decision, dict):
        raise _host_telemetry_error(
            "identity_mismatch",
            "Physical decision publication has no decision object.",
        )
    activation = decision.get("activation")
    if not isinstance(activation, dict):
        raise _host_telemetry_error(
            "identity_mismatch",
            "Physical decision publication cannot expose activation identity.",
        )
    try:
        vehicle_id = _host_require_id(decision.get("vehicle_id"), field="decision.vehicle_id")
        source_id = _host_require_id(decision.get("source_id"), field="decision.source_id")
        run_id = _host_require_id(decision.get("run_id"), field="decision.run_id")
        generation_id = _host_require_id(
            decision.get("generation_id"), field="decision.generation_id"
        )
        activation_engine_id = _host_require_id(
            activation.get("engine_id"), field="decision.activation.engine_id"
        )
        activation_at_ms = _host_require_epoch_ms(
            activation.get("activated_at_ms"), field="decision.activation.activated_at_ms"
        )
        activation_generation_id = _host_require_id(
            activation.get("generation_id"), field="decision.activation.generation_id"
        )
        source_frame = _decision_source_frame(decision)
    except HostTelemetryError as exc:
        if exc.reason == "identity_mismatch":
            raise
        raise _host_telemetry_error(
            "identity_mismatch",
            "Physical decision publication cannot expose exact telemetry identity.",
            details=exc.details,
        ) from exc
    if activation_engine_id != decision.get("activation_engine_id"):
        raise _host_telemetry_error(
            "identity_mismatch",
            "Physical decision activation engine identity does not match its envelope.",
        )
    if activation_at_ms != decision.get("activation_activated_at_ms"):
        raise _host_telemetry_error(
            "identity_mismatch",
            "Physical decision activation timestamp does not match its envelope.",
        )
    if activation_generation_id != generation_id:
        raise _host_telemetry_error(
            "identity_mismatch",
            "Physical decision activation generation does not match its envelope.",
        )
    if decision.get("frame_id") != source_frame["frame_id"]:
        raise _host_telemetry_error(
            "identity_mismatch",
            "Physical decision frame_id does not match its source-frame identity.",
        )
    if decision.get("frame_index") != source_frame["frame_index"]:
        raise _host_telemetry_error(
            "identity_mismatch",
            "Physical decision frame_index does not match its source-frame identity.",
        )
    if decision.get("timestamp_ms") != source_frame["captured_at_ms"]:
        raise _host_telemetry_error(
            "identity_mismatch",
            "Physical decision timestamp does not match its source-frame identity.",
        )
    return {
        "vehicle_id": vehicle_id,
        "source_id": source_id,
        "run_id": run_id,
        "generation_id": generation_id,
        "activation": {
            "engine_id": activation_engine_id,
            "activated_at_ms": activation_at_ms,
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
    vehicle_id: str | None = None,
) -> dict[str, Any]:
    """Join a normalized/latest telemetry point to a physical decision exactly."""

    if isinstance(telemetry, dict) and telemetry.get("schema") == HOST_TELEMETRY_SCHEMA:
        if now_ms is None:
            raise ValueError("now_ms is required when joining a raw telemetry record")
        telemetry = normalize_host_telemetry_record(
            telemetry,
            now_ms=now_ms,
            vehicle_id=vehicle_id,
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
    decision_identity = physical_decision_identity(normalized_decision)
    telemetry_identity = telemetry.get("identity")
    if not isinstance(telemetry_identity, dict) or telemetry_identity != decision_identity:
        raise _host_telemetry_error(
            "identity_mismatch",
            "Host telemetry identity does not exactly match the physical decision.",
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


def build_host_telemetry_capture(
    *,
    joined_point: dict[str, Any] | None,
    records_result: dict[str, Any] | None = None,
    vehicle_id: str | None = None,
) -> dict[str, Any]:
    """Build a separate telemetry capture envelope without changing decision bytes."""

    point = joined_point if isinstance(joined_point, dict) else host_telemetry_failure(
        "publisher_missing"
    )
    records = records_result if isinstance(records_result, dict) else None
    point_status = point.get("status")
    point_reason = point.get("reason") or ""
    coverage = (
        deepcopy(records.get("coverage"))
        if records is not None and isinstance(records.get("coverage"), dict)
        else {
            "status": "limited",
            "reason": "coverage_gap",
            "coverage_reason": "latest_point_only",
            "interval_covered": False,
            "record_count": 0,
        }
    )
    interval_covered = coverage.get("interval_covered") is True
    if point_status not in {"healthy", "limited"} or not point.get("joined"):
        status = point_status or "unavailable"
        reason = point_reason or "identity_mismatch"
        interval_covered = False
    elif not interval_covered:
        status = "limited"
        reason = (
            records.get("reason")
            if records is not None and records.get("reason")
            else "coverage_gap"
        )
    else:
        status = point_status
        reason = point_reason
    return {
        "schema": HOST_TELEMETRY_CAPTURE_SCHEMA,
        "status": status,
        "reason": reason,
        "vehicle_id": vehicle_id
        or (point.get("identity") or {}).get("vehicle_id"),
        "identity": deepcopy(point.get("identity")),
        "host_telemetry": deepcopy(point),
        "records": deepcopy(records.get("records", [])) if records is not None else [],
        "coverage": coverage,
        "interval_covered": interval_covered,
    }


def fetch_host_telemetry_capture(
    base_url: str,
    *,
    normalized_decision: dict[str, Any],
    vehicle_id: str,
    after_sequence: int = 0,
    limit: int = 128,
    now_ms: int | None = None,
    timeout_s: float = 3.0,
) -> dict[str, Any]:
    """Fetch a point plus bounded history for an additive telemetry artifact."""

    effective_now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    try:
        latest = fetch_host_telemetry_latest(base_url, timeout_s=timeout_s)
        normalized_latest = normalize_host_telemetry_record(
            latest,
            now_ms=effective_now_ms,
            vehicle_id=vehicle_id,
        )
        joined_point = join_host_telemetry_to_decision(
            normalized_latest,
            normalized_decision,
            vehicle_id=vehicle_id,
        )
    except (HostTelemetryError, ConnectionError, OSError, TypeError, ValueError) as exc:
        reason = exc.reason if isinstance(exc, HostTelemetryError) else "publisher_missing"
        joined_point = host_telemetry_failure(
            reason,
            message=str(exc),
            details=exc.details if isinstance(exc, HostTelemetryError) else {},
        )
    records_result: dict[str, Any] | None = None
    try:
        records_payload = fetch_host_telemetry_records(
            base_url,
            after_sequence=after_sequence,
            limit=limit,
            timeout_s=timeout_s,
        )
        records_result = normalize_host_telemetry_records(
            records_payload,
            now_ms=effective_now_ms,
            after_sequence=after_sequence,
            limit=limit,
            vehicle_id=vehicle_id,
        )
    except (HostTelemetryError, ConnectionError, OSError, TypeError, ValueError) as exc:
        reason = exc.reason if isinstance(exc, HostTelemetryError) else "publisher_missing"
        records_result = _host_records_result(
            status=_host_panel_status(reason),
            reason=reason,
            records=[],
            after_sequence=after_sequence,
            limit=limit,
            coverage={
                "status": "limited",
                "reason": reason,
                "interval_covered": False,
                "record_count": 0,
            },
            details=exc.details if isinstance(exc, HostTelemetryError) else {},
        )
    return build_host_telemetry_capture(
        joined_point=joined_point,
        records_result=records_result,
        vehicle_id=vehicle_id,
    )


# Short aliases keep the consumer seam discoverable without making the
# producer or accepted decision schemas depend on these helpers.
normalize_host_boundary_telemetry = normalize_host_telemetry_record
join_host_boundary_telemetry = join_host_telemetry_to_decision


def normalize_physical_decision_publication(
    publication: object,
    *,
    vehicle_id: str,
    now_ms: int,
    max_age_ms: int | None = None,
) -> dict[str, Any]:
    """Normalize and freshness-check a PiCar decision publication.

    The provider returns a detached view of the onboard transport and never
    creates vehicle/run/activation/frame identity. The typed cycle remains the
    existing ``shadow_decision_cycle_result_v0`` export for common validation.
    """

    if publication is None:
        raise _physical_decision_error(
            "missing",
            "Physical decision publication is missing.",
        )
    if not isinstance(publication, dict):
        raise _physical_decision_error(
            "incomplete",
            "Physical decision publication must be a JSON object.",
        )
    if publication.get("schema") != DECISION_PUBLICATION_SCHEMA:
        raise _physical_decision_error(
            "incomplete",
            f"Physical decision publication schema must be {DECISION_PUBLICATION_SCHEMA!r}.",
            field="schema",
        )
    try:
        vehicle_id = require_ascii_id(vehicle_id, field_name="vehicle_id")
    except ValueError as exc:
        raise ValueError("vehicle_id must be a valid identity") from exc
    if "status" not in publication or "ok" not in publication:
        raise _physical_decision_error(
            "incomplete",
            "Physical decision publication must include status and ok.",
        )
    status = publication.get("status")
    ok = publication.get("ok")
    if status != "ready" or ok is not True:
        reason = publication.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            reason = "unavailable"
        reason = {
            "mismatched_frame": "mismatched",
            "future": "future_dated",
            "stale": "expired",
        }.get(reason, reason)
        allowed = {
            "missing",
            "incomplete",
            "mismatched",
            "future_dated",
            "expired",
            "reset",
            "failed_step",
            "publisher_missing",
            "unavailable",
        }
        if reason not in allowed:
            reason = "unavailable"
        raise _physical_decision_error(
            reason,
            f"Physical decision publication is unavailable: {reason}.",
        )
    if publication.get("reason") != "":
        raise _physical_decision_error(
            "incomplete",
            "Ready physical decision publication must have an empty reason.",
            field="reason",
        )

    read_at_ms = _physical_required_int(
        publication.get("read_at_ms"), field="read_at_ms", allow_negative=True
    )
    result_age_ms = _physical_required_int(
        publication.get("result_age_ms"),
        field="result_age_ms",
        allow_negative=True,
    )
    advertised_ceiling = publication.get("stale_after_ms")
    if max_age_ms is None:
        if type(advertised_ceiling) is not int or advertised_ceiling <= 0:
            raise _physical_decision_error(
                "incomplete",
                "Physical decision publication stale_after_ms must be positive.",
                field="stale_after_ms",
            )
        ceiling = int(advertised_ceiling)
    else:
        if type(max_age_ms) is not int or max_age_ms <= 0:
            raise ValueError("max_age_ms must be a positive non-bool int")
        ceiling = int(max_age_ms)

    decision = _physical_require_mapping(publication.get("decision"), field="decision")
    required_decision_fields = (
        "vehicle_id",
        "source_id",
        "run_id",
        "activation_engine_id",
        "activation_activated_at_ms",
        "generation_id",
        "frame_id",
        "frame_index",
        "timestamp_ms",
        "published_at_ms",
        "activation",
        "cycle",
    )
    for field in required_decision_fields:
        if field not in decision:
            raise _physical_decision_error(
                "incomplete",
                f"Physical decision publication decision.{field} is missing.",
                field=f"decision.{field}",
            )

    decision_vehicle_id = _physical_required_id(
        decision.get("vehicle_id"), field="decision.vehicle_id"
    )
    if decision_vehicle_id != vehicle_id:
        raise _physical_decision_error(
            "mismatched",
            "Physical decision publication vehicle_id does not match the requested vehicle.",
            field="decision.vehicle_id",
        )
    source_id = _physical_required_id(decision.get("source_id"), field="decision.source_id")
    run_id = _physical_required_id(decision.get("run_id"), field="decision.run_id")
    activation_engine_id = _physical_required_id(
        decision.get("activation_engine_id"), field="decision.activation_engine_id"
    )
    generation_id = _physical_required_id(
        decision.get("generation_id"), field="decision.generation_id"
    )
    activation_activated_at_ms = _physical_required_int(
        decision.get("activation_activated_at_ms"),
        field="decision.activation_activated_at_ms",
    )
    frame_id = _physical_required_id(decision.get("frame_id"), field="decision.frame_id")
    frame_index = _physical_required_int(decision.get("frame_index"), field="decision.frame_index")
    timestamp_value = _physical_required_int(
        decision.get("timestamp_ms"), field="decision.timestamp_ms"
    )
    published_at_ms = _physical_required_int(
        decision.get("published_at_ms"), field="decision.published_at_ms", allow_negative=True
    )

    activation = _physical_require_mapping(decision.get("activation"), field="decision.activation")
    for field in ("engine_id", "activated_at_ms", "generation_id", "engine_config"):
        if field not in activation:
            raise _physical_decision_error(
                "incomplete",
                f"Physical decision publication decision.activation.{field} is missing.",
                field=f"decision.activation.{field}",
            )
    if activation.get("engine_id") != activation_engine_id:
        raise _physical_decision_error(
            "mismatched",
            "Decision activation engine identity does not match its outer identity.",
            field="decision.activation.engine_id",
        )
    if activation.get("activated_at_ms") != activation_activated_at_ms:
        raise _physical_decision_error(
            "mismatched",
            "Decision activation timestamp does not match its outer identity.",
            field="decision.activation.activated_at_ms",
        )
    if activation.get("generation_id") != generation_id:
        raise _physical_decision_error(
            "mismatched",
            "Decision activation generation does not match its outer identity.",
            field="decision.activation.generation_id",
        )
    if not isinstance(activation.get("engine_config"), dict):
        raise _physical_decision_error(
            "incomplete",
            "Physical decision activation engine_config must be an object.",
            field="decision.activation.engine_config",
        )

    cycle = _physical_require_mapping(decision.get("cycle"), field="decision.cycle")
    if cycle.get("schema") != "shadow_decision_cycle_result_v0" or cycle.get("status") != "ok":
        raise _physical_decision_error(
            "incomplete",
            "Physical decision cycle is not a successful shadow result.",
            field="decision.cycle",
        )
    if cycle.get("frame_id") != frame_id:
        raise _physical_decision_error(
            "mismatched",
            "Physical decision cycle frame_id does not match its outer identity.",
            field="decision.cycle.frame_id",
        )
    source = _physical_require_mapping(cycle.get("source"), field="decision.cycle.source")
    for field, expected in (
        ("frame_id", frame_id),
        ("frame_index", frame_index),
        ("timestamp_ms", timestamp_value),
    ):
        if source.get(field) != expected:
            raise _physical_decision_error(
                "mismatched",
                f"Physical decision source {field} does not match its outer identity.",
                field=f"decision.cycle.source.{field}",
            )
    if type(now_ms) is not int:
        raise ValueError("now_ms must be a non-bool int")
    age_ms = int(now_ms) - published_at_ms
    if age_ms < 0:
        raise _physical_decision_error(
            "future_dated",
            f"Physical decision publication is future-dated by {-age_ms} ms.",
            field="decision.published_at_ms",
        )
    if age_ms > ceiling:
        raise _physical_decision_error(
            "expired",
            f"Physical decision publication age {age_ms} ms exceeds {ceiling} ms.",
            field="decision.published_at_ms",
        )

    return {
        "publication": deepcopy(publication),
        "decision": deepcopy(decision),
        "vehicle_id": decision_vehicle_id,
        "source_id": source_id,
        "run_id": run_id,
        "activation_engine_id": activation_engine_id,
        "activation_activated_at_ms": activation_activated_at_ms,
        "generation_id": generation_id,
        "frame_id": frame_id,
        "frame_index": frame_index,
        "timestamp_ms": timestamp_value,
        "published_at_ms": published_at_ms,
        "read_at_ms": read_at_ms,
        "result_age_ms": age_ms,
        "max_age_ms": ceiling,
        "advertised_result_age_ms": result_age_ms,
    }


def fetch_observation_frame(
    base_url: str,
    *,
    timeout_s: float = 3.0,
) -> tuple[bytes, dict[str, str]]:
    url = f"{base_url.rstrip('/')}{LATEST_FRAME_PATH}"
    try:
        with urllib.request.urlopen(url, timeout=max(0.1, float(timeout_s))) as response:
            body = response.read()
            headers = {str(key).lower(): str(value) for key, value in response.headers.items()}
            status_code = getattr(response, "status", 200)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")
        except Exception:
            detail = ""
        raise ConnectionError(
            f"GET {url} failed with HTTP {exc.code}"
            + (f": {detail[:240]}" if detail else "")
        ) from exc
    except urllib.error.URLError as exc:
        raise ConnectionError(f"GET {url} failed: {exc.reason}") from exc
    except TimeoutError as exc:
        raise ConnectionError(f"GET {url} timed out after {timeout_s}s") from exc

    if status_code >= 400 or not body:
        raise ConnectionError(f"GET {url} returned HTTP {status_code} with no image body")
    return body, headers


def frame_id_from_publication(publication: dict[str, Any]) -> str | None:
    """Return the publication's frame identity when present."""

    frame = publication.get("frame") if isinstance(publication.get("frame"), dict) else {}
    frame_id = frame.get("frame_id")
    if isinstance(frame_id, str) and frame_id.strip():
        return frame_id.strip()
    return None


def frame_id_from_headers(headers: dict[str, str]) -> str | None:
    """Return X-Frame-Id (case-insensitive) from an observation frame response."""

    raw = headers.get("x-frame-id") or headers.get("X-Frame-Id")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def fetch_matched_observation_pair(
    base_url: str,
    *,
    timeout_s: float = 3.0,
    match_timeout_s: float = 3.0,
    require_image: bool = True,
    after_frame_id: str | None = None,
) -> dict[str, Any]:
    """Fetch latest publication + JPEG and require matching frame identities.

    Separate GETs can race. Retry until ``X-Frame-Id`` equals the publication
    ``frame.frame_id`` (or until timeout). When ``require_image`` is true, a
    nonempty JPEG body is required; publications without an image keep polling
    instead of succeeding empty. When ``after_frame_id`` is set, the matched
    pair must use a different frame id. Raises ``TimeoutError`` when a verified
    pair cannot be obtained.
    """

    deadline = time.monotonic() + max(0.2, float(match_timeout_s))
    last_error: str | None = None
    attempts = 0
    previous = (after_frame_id or "").strip() or None
    while time.monotonic() < deadline:
        attempts += 1
        try:
            publication = fetch_observation_publication(base_url, timeout_s=timeout_s)
        except ConnectionError as exc:
            last_error = str(exc)
            time.sleep(0.05)
            continue

        pub_frame_id = frame_id_from_publication(publication)
        frame = publication.get("frame") if isinstance(publication.get("frame"), dict) else {}
        has_image = True
        if "has_image" in frame:
            has_image = bool(frame.get("has_image"))

        if not require_image:
            if not pub_frame_id:
                last_error = "publication has no frame.frame_id"
                time.sleep(0.05)
                continue
            if previous is not None and pub_frame_id == previous:
                last_error = f"publication frame_id still {pub_frame_id!r}; waiting for newer frame"
                time.sleep(0.05)
                continue
            return {
                "publication": publication,
                "frame_bytes": None,
                "frame_headers": {},
                "frame_id": pub_frame_id,
                "matched": True,
                "attempts": attempts,
                "image_required": False,
            }

        # require_image=True: never succeed without a real JPEG body.
        if not has_image:
            last_error = "publication has_image=false; waiting for a frame with an image"
            time.sleep(0.05)
            continue

        if not pub_frame_id:
            last_error = "publication has no frame.frame_id"
            time.sleep(0.05)
            continue

        if previous is not None and pub_frame_id == previous:
            last_error = f"publication frame_id still {pub_frame_id!r}; waiting for newer frame"
            time.sleep(0.05)
            continue

        try:
            frame_bytes, headers = fetch_observation_frame(base_url, timeout_s=timeout_s)
        except ConnectionError as exc:
            last_error = str(exc)
            time.sleep(0.05)
            continue

        if not frame_bytes:
            last_error = "frame response body is empty"
            time.sleep(0.05)
            continue

        jpeg_frame_id = frame_id_from_headers(headers)
        if jpeg_frame_id is None:
            last_error = "frame response missing X-Frame-Id"
            time.sleep(0.05)
            continue
        if jpeg_frame_id != pub_frame_id:
            last_error = (
                f"frame pair mismatch publication={pub_frame_id!r} jpeg={jpeg_frame_id!r}"
            )
            time.sleep(0.05)
            continue
        if previous is not None and jpeg_frame_id == previous:
            last_error = f"jpeg frame_id still {jpeg_frame_id!r}; waiting for newer frame"
            time.sleep(0.05)
            continue

        return {
            "publication": publication,
            "frame_bytes": frame_bytes,
            "frame_headers": headers,
            "frame_id": pub_frame_id,
            "matched": True,
            "attempts": attempts,
            "image_required": True,
        }

    raise TimeoutError(
        f"Timed out after {match_timeout_s}s waiting for a matched publication/JPEG pair"
        + (f": {last_error}" if last_error else "")
        + f" (attempts={attempts})"
    )


def publication_to_frame_record(publication: dict[str, Any]) -> dict[str, Any]:
    """Adapt an onboard publication and its retained evidence.

    The memory payload originates at shared_memory["decision.snapshot"].
    """
    frame = publication.get("frame") if isinstance(publication.get("frame"), dict) else {}
    perception = publication.get("perception")
    observation = publication.get("observation")
    control = publication.get("control")
    completed_at_ms = frame.get("completed_at_ms")
    duration_ms = publication.get("duration_ms")
    memory = publication.get("memory")
    return {
        "frame_id": frame.get("frame_id"),
        "frame_index": frame.get("frame_index"),
        "captured_at_ms": frame.get("captured_at_ms"),
        "perception_completed_at_ms": completed_at_ms,
        "perception_duration_ms": duration_ms,
        "cycle_duration_ms": duration_ms,
        "perception": perception if isinstance(perception, dict) else None,
        "observation": observation if isinstance(observation, dict) else None,
        "memory": memory if isinstance(memory, dict) else None,
        "control": control if isinstance(control, dict) else None,
        "engine": publication.get("engine"),
        "algorithm": publication.get("algorithm"),
        "health": publication.get("health"),
        "result_age_ms": publication.get("result_age_ms"),
        "action_policy": "observe_only",
        "control_source": "physical_onboard",
        "control_application": "donkey_drive_mode",
    }


def perception_text_from_publication(publication: dict[str, Any]) -> str:
    perception = publication.get("perception")
    if isinstance(perception, dict):
        text = perception.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()
        lines = perception.get("lines")
        if isinstance(lines, list) and lines:
            return "\n".join(str(line) for line in lines)
        status = perception.get("status")
        thing_count = len(perception.get("things") or []) if isinstance(perception.get("things"), list) else 0
        signal_count = (
            len(perception.get("signals") or []) if isinstance(perception.get("signals"), list) else 0
        )
        return (
            f"perception status={status or 'unknown'} "
            f"signals={signal_count} things={thing_count}"
        )

    health = publication.get("health") or "unknown"
    error = publication.get("error")
    if error:
        return f"health={health}\nerror={error}"
    return f"health={health}\n(no perception payload in latest snapshot)"


def picar_base_url(vehicle: dict[str, Any]) -> str | None:
    connection = vehicle.get("connection") if isinstance(vehicle.get("connection"), dict) else {}
    base = connection.get("base_url")
    return base.rstrip("/") if isinstance(base, str) and base.strip() else None
