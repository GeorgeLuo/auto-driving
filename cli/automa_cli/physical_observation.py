from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from copy import deepcopy
from pathlib import Path
from typing import Any

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
PHYSICAL_RUNTIME_DIRNAME = "physical_observation"
DECISION_PUBLICATION_SCHEMA = "automa_physical_decision_publication_v0"


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
    """Adapt onboard publication JSON to the local perception-view frame record."""
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
