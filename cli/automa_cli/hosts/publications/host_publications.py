"""What a runtime host publishes, read over HTTP.

Its autonomy status, completed frames and host telemetry. A PiCar's host and a local Chase host serve the same routes;
``frame_publication`` and ``host_telemetry`` check what these reads return.
"""

from __future__ import annotations

import base64
import binascii
import json
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from copy import deepcopy

from autonomy.runtime.frame_loop import (
    FRAME_TRANSACTION_SCHEMA,
    FramePublication,
    LATEST_JSON_PATH,
    OBSERVATION_TRANSACTION_PATH,
)
from autonomy.runtime.routes import (
    STATUS_PATH,
    TELEMETRY_LATEST_PATH,
)
from ...shared.io import http_json
from .host_telemetry import HostTelemetryError


@dataclass
class TransactionCache:
    etag: str | None = None
    payload: dict[str, object] | None = None
    image: tuple[bytes, str] | None = None

    def clear(self) -> None:
        self.etag = None
        self.payload = None
        self.image = None


def _cached_transaction(cache: TransactionCache, read_at_ms: int | None = None) -> dict[str, object]:
    payload = deepcopy(cache.payload)
    if payload is None:
        raise ConnectionError("Host returned 304 without a cached transaction")
    publication = payload.get("publication")
    if not isinstance(publication, FramePublication):
        raise ConnectionError("Host returned 304 without a cached transaction")
    if read_at_ms is not None:
        age = publication.result_age_ms
        if publication.report is not None:
            age = max(0, read_at_ms - publication.report.published_at_ms)
        payload["publication"] = replace(publication, read_at_ms=read_at_ms, result_age_ms=age)
    return payload


def _json_object(body: bytes, url: str, status_code: int) -> dict[str, object]:
    payload = http_json.json_object(body, url, status_code)
    payload.setdefault("http_status", status_code)
    return payload


def _get_json(base_url: str, path: str, *, timeout_s: float) -> dict[str, object]:
    """Read a JSON object; an HTTP error's JSON body is the answer."""
    url = f"{base_url.rstrip('/')}{path}"
    status_code, _headers, body = http_json.request(url, timeout_s=timeout_s)
    return _json_object(body, url, status_code)


def _get_transaction(base_url: str, path: str, *, timeout_s: float,
                     cache: TransactionCache | None) -> dict[str, object]:
    """Read a transaction under the current conditional-image contract.

    Only a 304 keeps the cache; every other outcome clears it, and a 200
    refills it.
    """
    url = f"{base_url.rstrip('/')}{path}"
    headers = {"If-None-Match": cache.etag} if cache and cache.etag else None
    try:
        status_code, response_headers, body = http_json.request(url, timeout_s=timeout_s, headers=headers)
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


def fetch_autonomy_status(base_url: str, *, timeout_s: float = 3.0) -> dict[str, object]:
    """GET /host/status from a runtime host."""

    return _get_json(base_url, STATUS_PATH, timeout_s=timeout_s)


def host_state(base_url: str, *, host_name: str | None = None, timeout_s: float) -> dict[str, object]:
    """A runtime host's state as its ``/host/status`` reports it; never raises.

    ``state`` is the session status the host reports, ``not answering`` when no
    reply arrives, or ``error`` when the reply is not this host's status.
    ``error`` says why, or is the host's last cycle error. ``reply`` is the
    status object the host answered with.
    """

    result: dict[str, object] = {
        "state": "not answering", "mode": None, "decision_count": None, "error_count": None,
        "last_frame_at_ms": None, "error": None, "reply": None,
    }
    url = f"{base_url.rstrip('/')}{STATUS_PATH}"
    try:
        status_code, _headers, body = http_json.request(url, timeout_s=timeout_s)
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
    latest = observation_component(autonomy).get("latest")
    completed_at_ms = latest.get("completed_at_ms") if isinstance(latest, dict) else None
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


def observation_component(autonomy: dict[str, object]) -> dict[str, object]:
    """The observation component a host's ``/host/status`` ``host`` reports, or ``{}``."""

    components = autonomy.get("components") if isinstance(autonomy.get("components"), dict) else {}
    observation = components.get("observation")
    return observation if isinstance(observation, dict) else {}


def _no_answer(exc: ConnectionError, timeout_s: float) -> str:
    cause = getattr(exc.__cause__, "reason", exc.__cause__)
    if isinstance(cause, TimeoutError):
        return f"no answer within {max(0.1, float(timeout_s)):.1f}s"
    return getattr(cause, "strerror", None) or str(cause or exc)


def _parse_frame(publication: object) -> FramePublication:
    try:
        return FramePublication.from_dict(publication)
    except (TypeError, ValueError) as exc:
        raise ConnectionError(f"Invalid host report: {exc}") from exc


def fetch_frame_publication(base_url: str, *, timeout_s: float = 3.0) -> FramePublication:
    """Read the host's completed frame envelope."""

    return _parse_frame(_get_json(base_url, LATEST_JSON_PATH, timeout_s=timeout_s))


def _frame_from_transaction(
    payload: dict[str, object], cache: TransactionCache | None,
) -> FramePublication:
    publication = payload.get("publication")
    if isinstance(publication, FramePublication):
        return publication
    if payload.get("http_status") == 503 and isinstance(publication, dict) and (
        payload.get("schema") != FRAME_TRANSACTION_SCHEMA
    ):
        return _parse_frame(publication)
    if not isinstance(publication, dict) or payload.get("schema") != FRAME_TRANSACTION_SCHEMA:
        raise ConnectionError("Invalid frame transaction")
    try:
        frame = _parse_frame(publication)
    except ConnectionError:
        if cache is not None:
            cache.clear()
        raise
    if cache is not None and cache.payload is not None:
        cache.payload["publication"] = frame
    return frame


def fetch_frame_sample(
    base_url: str, *, timeout_s: float = 3.0, cache: TransactionCache | None = None,
) -> tuple[FramePublication, bytes | None]:
    """Read one completed frame and, when the JPEG names that frame, its image."""
    payload = _get_transaction(base_url, OBSERVATION_TRANSACTION_PATH, timeout_s=timeout_s, cache=cache)
    frame = _frame_from_transaction(payload, cache)
    report = frame.report
    if payload.get("http_status") == 503 or report is None:
        return frame, None
    encoded = payload.get("image_base64")
    image_matches = (
        isinstance(encoded, str)
        and encoded
        and payload.get("image_error") in (None, "")
        and payload.get("image_content_type") == "image/jpeg"
        and payload.get("image_frame_id") == report.frame_id
    )
    if not image_matches or not isinstance(encoded, str):
        return frame, None
    if cache is not None and cache.image is not None:
        return frame, cache.image[0]
    try:
        jpeg = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        return frame, None
    if not jpeg:
        return frame, None
    if cache is not None:
        cache.image = jpeg, "image/jpeg"
    return frame, jpeg


class _RejectTelemetryRedirect(urllib.request.HTTPRedirectHandler):
    """Keep the telemetry client on the exact read-only route it requested."""

    def redirect_request(  # noqa: PLR0917 - urllib's redirect hook takes these arguments
        self,
        req: urllib.request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        del req, fp, code, msg, headers, newurl
        return None


def _fetch_host_telemetry_json(
    url: str,
    *,
    timeout_s: float,
) -> dict[str, object]:
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
            body = b"" if 300 <= status_code < 400 else exc.read()
        if 300 <= status_code < 400:
            raise HostTelemetryError(
                "redirect_rejected",
                f"GET {url} returned a redirect that the telemetry client rejected.",
            ) from exc
        if not body:
            reason = (
                "method_not_allowed"
                if status_code == 405
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
) -> dict[str, object]:
    """GET the latest host-boundary telemetry point without following redirects."""

    url = f"{base_url.rstrip('/')}{TELEMETRY_LATEST_PATH}"
    return _fetch_host_telemetry_json(url, timeout_s=timeout_s)
