"""The CLI's JSON requests to a host or catalog route.

An HTTP error status is a reply. A request that is refused, dropped, garbled,
or not answered in time raises ``ConnectionError`` with the original failure as
its cause.
"""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.request
from typing import Any


def request(url: str, *, timeout_s: float, payload: dict[str, Any] | None = None,
            headers: dict[str, str] | None = None) -> tuple[int, Any, bytes]:
    """Send once and read the body: a GET, or a POST of ``payload`` as JSON."""
    method = "GET" if payload is None else "POST"
    timeout_s = max(0.1, float(timeout_s))
    try:
        if payload is not None:
            headers = {"Content-Type": "application/json", **(headers or {})}
            target = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers)
        else:
            target = urllib.request.Request(url, headers=headers) if headers else url
        try:
            response = urllib.request.urlopen(target, timeout=timeout_s)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return getattr(response, "status", 200), getattr(response, "headers", {}) or {}, response.read()
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise ConnectionError(f"{method} {url} did not answer within {timeout_s:g} s") from exc
        raise ConnectionError(f"{method} {url} failed: {exc.reason}") from exc
    except TimeoutError as exc:
        raise ConnectionError(f"{method} {url} did not answer within {timeout_s:g} s") from exc
    except (OSError, ValueError, http.client.HTTPException) as exc:
        # A dropped or garbled reply escapes urlopen unwrapped.
        raise ConnectionError(f"{method} {url} failed: {exc}") from exc


def json_object(body: bytes, url: str, status: int, *, method: str = "GET") -> dict[str, Any]:
    """The JSON object a reply carries; an error status without one names the status."""
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        problem = f"returned HTTP {status}" if status >= 400 else "returned non-JSON body"
        raise ConnectionError(f"{method} {url} {problem}") from exc
    if not isinstance(payload, dict):
        problem = f"returned HTTP {status}" if status >= 400 else "returned a non-object JSON payload"
        raise ConnectionError(f"{method} {url} {problem}")
    return payload


def request_json(url: str, *, timeout_s: float,
                 payload: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    """The status and JSON object of one request; an HTTP error's JSON body is the answer."""
    status, _headers, body = request(url, timeout_s=timeout_s, payload=payload)
    return status, json_object(body, url, status, method="GET" if payload is None else "POST")
