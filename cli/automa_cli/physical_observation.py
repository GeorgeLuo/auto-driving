from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .paths import safe_path_part
from .perception_view import get_perception_view_status


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ROOT = Path(os.environ.get("AUTOMA_RUNTIME_ROOT", ROOT / "runtime" / "vehicles"))

LATEST_JSON_PATH = "/autonomy/observation/latest"
LATEST_FRAME_PATH = "/autonomy/observation/latest/frame.jpg"
STATUS_JSON_PATH = "/autonomy/status"
PHYSICAL_RUNTIME_DIRNAME = "physical_observation"


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
