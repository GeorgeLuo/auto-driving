"""What a runtime host publishes, read over HTTP.

Its autonomy status, observation and decision publications, frames and host
telemetry. A PiCar's host and a local Chase host serve the same routes;
``decision_publication`` and ``host_telemetry`` check what these reads return.
"""

from __future__ import annotations

import base64
import binascii
import http.client
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from copy import deepcopy
from typing import Any
from urllib.parse import urlencode

from autonomy.runtime.frame_loop import (
    DECISION_LATEST_PATH,
    DECISION_TRANSACTION_PATH,
    LATEST_JSON_PATH,
    OBSERVATION_TRANSACTION_PATH,
    OBSERVATION_TRANSACTION_SCHEMA,
)
from autonomy.runtime.routes import (
    STATUS_PATH,
    TELEMETRY_LATEST_PATH,
    TELEMETRY_RECORDS_PATH,
)
from .host_telemetry import HostTelemetryError


@dataclass
class TransactionCache:
    etag: str | None = None
    payload: dict[str, Any] | None = None
    image: tuple[bytes, str] | None = None

    def clear(self) -> None:
        self.etag = None
        self.payload = None
        self.image = None


def _cached_transaction(cache: TransactionCache, read_at_ms: int | None = None) -> dict[str, Any]:
    payload = deepcopy(cache.payload)
    if payload is None:
        raise ConnectionError("Host returned 304 without a cached transaction")
    publication = payload.get("publication") or {}
    if read_at_ms is not None:
        publication["read_at_ms"] = read_at_ms
        frame = publication.get("frame") or {}
        decision = publication.get("decision") or {}
        completed = decision.get("published_at_ms", frame.get("completed_at_ms"))
        if type(completed) is int:
            publication["result_age_ms"] = max(0, read_at_ms - completed)
    return payload


def _request(url: str, *, timeout_s: float,
             headers: dict[str, str] | None = None) -> tuple[int, Any, bytes]:
    """GET once and read the body; an HTTP error status is a response."""
    target = urllib.request.Request(url, headers=headers) if headers else url
    try:
        try:
            response = urllib.request.urlopen(target, timeout=max(0.1, float(timeout_s)))
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return getattr(response, "status", 200), getattr(response, "headers", {}) or {}, response.read()
    except urllib.error.URLError as exc:
        raise ConnectionError(f"GET {url} failed: {exc.reason}") from exc
    except TimeoutError as exc:
        raise ConnectionError(f"GET {url} timed out after {timeout_s}s") from exc
    except (OSError, http.client.HTTPException) as exc:
        # A dropped or garbled reply escapes urlopen unwrapped.
        raise ConnectionError(f"GET {url} failed: {exc}") from exc


def _json_object(body: bytes, url: str, status_code: int) -> dict[str, Any]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConnectionError(f"GET {url} returned non-JSON body") from exc
    if not isinstance(payload, dict):
        raise ConnectionError(f"GET {url} returned a non-object JSON payload")
    payload.setdefault("http_status", status_code)
    return payload


def _get_json(base_url: str, path: str, *, timeout_s: float) -> dict[str, Any]:
    """Read a JSON object; an HTTP error's JSON body is the answer."""
    url = f"{base_url.rstrip('/')}{path}"
    status_code, _headers, body = _request(url, timeout_s=timeout_s)
    return _json_object(body, url, status_code)


def _get_transaction(base_url: str, path: str, *, timeout_s: float,
                     cache: TransactionCache | None) -> dict[str, Any]:
    """Read a transaction under the current conditional-image contract.

    Only a 304 keeps the cache; every other outcome clears it, and a 200
    refills it.
    """
    url = f"{base_url.rstrip('/')}{path}"
    headers = {"If-None-Match": cache.etag} if cache and cache.etag else None
    try:
        status_code, response_headers, body = _request(url, timeout_s=timeout_s, headers=headers)
        if status_code == 304:
            if cache is None or cache.payload is None:
                raise ConnectionError("Host returned 304 without a cached transaction")
            read_at = response_headers.get("X-Read-At-Ms")
            return _cached_transaction(cache, int(read_at) if read_at is not None else None)
        if status_code == 404:
            raise ConnectionError(f"Incompatible host: GET {url} is missing; update the host.")
        if status_code not in {200, 503}:
            raise ConnectionError(f"GET {url} failed with HTTP {status_code}")
        etag = response_headers.get("ETag")
        if status_code == 200 and not etag:
            raise ConnectionError(f"Incompatible host: GET {url} returned no ETag; update the host.")
        payload = _json_object(body, url, status_code)
    except Exception:
        if cache is not None:
            cache.clear()
        raise
    if cache is not None:
        cache.clear()
        if status_code == 200:
            cache.etag, cache.payload = etag, deepcopy(payload)
    return payload


def fetch_autonomy_status(base_url: str, *, timeout_s: float = 3.0) -> dict[str, Any]:
    """GET /host/status from a runtime host."""

    return _get_json(base_url, STATUS_PATH, timeout_s=timeout_s)


def host_state(base_url: str, *, host_name: str | None = None, timeout_s: float) -> dict[str, Any]:
    """A runtime host's state as its ``/host/status`` reports it; never raises.

    ``state`` is the session status the host reports, ``not answering`` when no
    reply arrives, or ``error`` when the reply is not this host's status.
    ``error`` says why, or is the host's last cycle error. ``reply`` is the
    status object the host answered with.
    """

    result: dict[str, Any] = {
        "state": "not answering", "mode": None, "decision_count": None, "error_count": None,
        "last_frame_at_ms": None, "error": None, "reply": None,
    }
    url = f"{base_url.rstrip('/')}{STATUS_PATH}"
    try:
        status_code, _headers, body = _request(url, timeout_s=timeout_s)
    except ConnectionError as exc:
        return {**result, "error": _no_answer(exc, timeout_s)}
    no_host = f"{base_url} answers but reports no runtime host"
    try:
        reply = _json_object(body, url, status_code)
    except ConnectionError:
        return {**result, "state": "error", "error": no_host}
    result["reply"] = reply
    if reply.get("ok") is not True:
        return {**result, "state": "error", "error": str(reply.get("error") or f"HTTP {status_code}")}
    if host_name is not None and reply.get("host_name") not in (None, host_name):
        return {**result, "state": "error", "error": f"{base_url} answers as {reply['host_name']}"}
    session = reply.get("session") if isinstance(reply.get("session"), dict) else {}
    autonomy = reply.get("host") if isinstance(reply.get("host"), dict) else {}
    if not autonomy or not isinstance(session.get("status"), str):
        return {**result, "state": "error", "error": no_host}
    components = autonomy.get("components") if isinstance(autonomy.get("components"), dict) else {}
    observation = components.get("observation") if isinstance(components.get("observation"), dict) else {}
    latest = observation.get("latest") if isinstance(observation.get("latest"), dict) else {}
    completed_at_ms = latest.get("completed_at_ms")
    return {
        **result,
        "state": session["status"],
        "mode": reply.get("mode"),
        "decision_count": session.get("processed_decisions"),
        "error_count": autonomy.get("error_count"),
        "last_frame_at_ms": completed_at_ms if type(completed_at_ms) is int else None,
        # The host keeps it after a stop, until the next start or good cycle.
        "error": autonomy.get("last_error"),
    }


def _no_answer(exc: ConnectionError, timeout_s: float) -> str:
    cause = getattr(exc.__cause__, "reason", exc.__cause__)
    if isinstance(cause, TimeoutError):
        return f"no answer within {max(0.1, float(timeout_s)):.1f}s"
    return getattr(cause, "strerror", None) or str(cause or exc)


def fetch_observation_publication(base_url: str, *, timeout_s: float = 3.0) -> dict[str, Any]:
    """GET the latest observation publication from a runtime host."""

    return _get_json(base_url, LATEST_JSON_PATH, timeout_s=timeout_s)


def fetch_observation_sample(
    base_url: str, *, timeout_s: float = 3.0, cache: TransactionCache | None = None,
) -> tuple[dict[str, Any], bytes | None]:
    """Read observation metadata and its image from one host transaction."""
    payload = _get_transaction(base_url, OBSERVATION_TRANSACTION_PATH, timeout_s=timeout_s, cache=cache)
    publication = payload.get("publication")
    if payload.get("http_status") == 503:
        if isinstance(publication, dict):
            return publication, None
        raise ConnectionError("Observation transaction returned HTTP 503")
    if payload.get("schema") != OBSERVATION_TRANSACTION_SCHEMA or not isinstance(publication, dict):
        raise ValueError("Invalid observation transaction")
    frame = publication.get("frame") or {}
    encoded = payload.get("image_base64")
    if encoded is None and not frame.get("has_image"):
        return publication, None
    if (
        not isinstance(encoded, str)
        or payload.get("image_content_type") != "image/jpeg"
        or not frame.get("frame_id")
        or payload.get("image_frame_id") != frame["frame_id"]
    ):
        raise ValueError("Observation transaction image does not match its frame")
    if cache is not None and cache.image is not None:
        return publication, cache.image[0]
    try:
        jpeg = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("Invalid observation transaction image encoding") from exc
    if not jpeg:
        raise ValueError("Empty observation transaction image")
    if cache is not None:
        cache.image = jpeg, "image/jpeg"
    return publication, jpeg


def fetch_decision_publication(base_url: str, *, timeout_s: float = 3.0) -> dict[str, Any]:
    """GET the read-only current decision publication from a runtime host."""

    return _get_json(base_url, DECISION_LATEST_PATH, timeout_s=timeout_s)


def fetch_decision_transaction(base_url: str, *, timeout_s: float = 3.0,
                               cache: TransactionCache | None = None) -> dict[str, Any]:
    """GET the current decision and its image from one host transaction."""

    return _get_transaction(base_url, DECISION_TRANSACTION_PATH, timeout_s=timeout_s, cache=cache)


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
        with exc:
            body = exc.read() if exc.fp is not None and not 300 <= status_code < 400 else b""
        if 300 <= status_code < 400:
            raise HostTelemetryError(
                "redirect_rejected",
                f"GET {url} returned a redirect that the telemetry client rejected.",
            ) from exc
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

    url = f"{base_url.rstrip('/')}{TELEMETRY_LATEST_PATH}"
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
    url = f"{base_url.rstrip('/')}{TELEMETRY_RECORDS_PATH}?{query}"
    return _fetch_host_telemetry_json(url, timeout_s=timeout_s, query_route=True)
