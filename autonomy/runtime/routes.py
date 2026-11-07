"""The HTTP contract every vehicle runtime serves.

``RuntimeRoutes.handle`` maps one request onto a ``FrameLoop`` and its cycle
host and returns ``(status, headers, body)``. Transports only move bytes:
the PiCar serves this table from Donkey's Tornado app, the Chase host from a
stdlib server. The CLI reads every vehicle through the same routes.
"""
from __future__ import annotations

import base64
import json
import logging
import threading
from collections.abc import Mapping
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from autonomy.runtime.frame_loop import (
    FRAME_TRANSACTION_SCHEMA,
    FrameLoop,
    LATEST_JSON_PATH,
    OBSERVATION_TRANSACTION_PATH,
)
from autonomy.runtime.session import RunConfiguration
from autonomy.runtime.http_errors import error_payload, write_error

logger = logging.getLogger(__name__)

STATUS_PATH = "/host/status"
RUNTIME_PATH = "/host/runtime"
PLUGINS_PATH = "/api/plugins"
TELEMETRY_LATEST_PATH = "/host/telemetry/latest"
TELEMETRY_SCHEMA = "automa_host_boundary_telemetry"
RESTART_DELAY_S = 0.2

Response = tuple[int, dict[str, str], bytes]


def json_response(payload: dict[str, Any], status: int = 200) -> Response:
    body = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return status, {"Content-Type": "application/json", "Cache-Control": "no-store"}, body


class RuntimeRoutes:
    """Serve one runtime's status, lifecycle, catalog, and publications.

    ``telemetry`` is the optional host-boundary observer (``latest``).
    ``on_restart`` replaces the process after a restart response is sent;
    the service manager starts a fresh host.
    """

    def __init__(
        self,
        loop: FrameLoop,
        *,
        telemetry: Any | None = None,
        on_restart: Callable[[], None] | None = None,
    ) -> None:
        self.loop = loop
        self.host = loop.host
        self.telemetry = telemetry
        self.on_restart = on_restart
        self._routes: dict[tuple[str, str], Callable[[dict[str, list[str]], bytes, Mapping[str, str]], Response]] = {
            ("GET", STATUS_PATH): self._status,
            ("GET", RUNTIME_PATH): self._runtime,
            ("POST", RUNTIME_PATH): self._runtime_command,
            ("GET", PLUGINS_PATH): self._catalog,
            ("POST", PLUGINS_PATH): self._catalog,
            ("GET", LATEST_JSON_PATH): self._observation,
            ("GET", OBSERVATION_TRANSACTION_PATH): self._transaction,
            ("GET", TELEMETRY_LATEST_PATH): self._telemetry_latest,
        }
        self._paths = {path for _, path in self._routes}

    def handles(self, path: str) -> bool:
        return urlsplit(path).path in self._paths

    def handle(self, method: str, target: str, body: bytes = b"", *,
               headers: Mapping[str, str] | None = None, origin: str = "") -> Response:
        """Answer one request; ``target`` is the path with its query string."""

        parts = urlsplit(target)
        path = parts.path.rstrip("/") or "/"
        method = "GET" if method.upper() == "HEAD" else method.upper()
        route = self._routes.get((method, path))
        if route is None:
            if path in self._paths:
                return json_response(error_payload(f"{method} not allowed on {path}"), 405)
            return json_response(error_payload(f"no runtime route {path}"), 404)
        if method == "POST":
            # Every runtime write changes state a page on another site must not reach.
            refusal = write_error(headers or {}, origin=origin)
            if refusal is not None:
                status, payload = refusal
                return json_response(payload, status)
        try:
            query = parse_qs(parts.query, keep_blank_values=True, strict_parsing=False)
            return route(query, body or b"", headers or {})
        except Exception as exc:  # noqa: BLE001 - one bad request must not end the host
            logger.exception("Runtime route %s %s failed", method, path)
            return json_response(error_payload(f"{type(exc).__name__}: {exc}"), 500)

    def status_payload(self) -> dict[str, Any]:
        session = self.host.session_status()
        return {
            "ok": True,
            "host_run_id": self.loop.run_id,
            "runtime": self.loop.runtime,
            "host_name": self.loop.host_name,
            "mode": session["execution"]["mode"],
            "session": session,
            "host": self.host.status(),
        }

    @staticmethod
    def _json_body(body: bytes) -> dict[str, Any]:
        if not body:
            return {}
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"request body is not JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        return payload

    def _status(self, query: dict[str, list[str]], body: bytes, headers: Mapping[str, str]) -> Response:
        return json_response(self.status_payload())

    def _runtime(self, query: dict[str, list[str]], body: bytes, headers: Mapping[str, str]) -> Response:
        return json_response({
            "ok": True,
            "host_run_id": self.loop.run_id,
            "session": self.host.session_status(),
        })

    def _runtime_command(self, query: dict[str, list[str]], body: bytes, headers: Mapping[str, str]) -> Response:
        try:
            data = self._json_body(body)
            command = data.get("command")
            if command == "run":
                self.loop.start(
                    RunConfiguration.from_dict(data.get("configuration")), record=bool(data.get("record", False))
                )
            elif command == "read_recording":
                recording = self.host.recording
                if recording is None:
                    raise ValueError("no recorded run is available")
                return json_response({
                    "ok": True,
                    "recording": recording.read(run_id=data.get("run_id"), after=data.get("after")),
                })
            elif command in {"stop", "restart"}:
                self.loop.stop()
            else:
                raise ValueError(f"unknown runtime command {command!r}")
        except (TypeError, ValueError, RuntimeError) as exc:
            return json_response(error_payload(str(exc)), 400)
        response = self._runtime(query, body, headers)
        if command == "restart":
            if self.on_restart is None:
                return json_response(error_payload("this runtime cannot restart itself"), 400)
            threading.Timer(RESTART_DELAY_S, self.on_restart).start()
        return response

    def _catalog(self, query: dict[str, list[str]], body: bytes, headers: Mapping[str, str]) -> Response:
        try:
            payload = self._json_body(body) if body else None
        except ValueError as exc:
            return json_response(error_payload(str(exc)), 400)
        # Uploads last for this host run; the id shows when a restart cleared them.
        result = {**self.host.plugin_catalog(payload), "host_run_id": self.loop.run_id}
        return json_response(result, 200 if result.get("ok") else 400)

    def _observation(self, query: dict[str, list[str]], body: bytes, headers: Mapping[str, str]) -> Response:
        payload = self.loop.publish_latest()
        return json_response(payload, 503 if payload.get("report") is None else 200)

    def _transaction(self, query: dict[str, list[str]], body: bytes,
                     request_headers: Mapping[str, str]) -> Response:
        publication, capture, revision = self.loop.read_transaction()
        report = publication.get("report") if isinstance(publication.get("report"), dict) else None
        state = publication.get("health")
        etag = f'W/"{revision}:{state}"'
        headers = {"Cache-Control": "no-cache", "ETag": etag,
                   "X-Read-At-Ms": str(publication["read_at_ms"])}
        matched = next((value for key, value in request_headers.items()
                        if key.lower() == "if-none-match"), None)
        image_ready = capture is not None and capture.jpeg_cache.data is not None
        if report is not None and matched == etag and image_ready:
            return 304, headers, b""
        jpeg = None
        image_error = None
        if report is None:
            image_error = state or "absent"
        elif capture is None or (capture.image is None and capture.jpeg_cache.data is None):
            image_error = "image_unavailable"
        else:
            # The capture is the state the report was read from, so they name one frame.
            try:
                jpeg = self.loop.frame_jpeg(capture)
            except Exception as exc:
                image_error = f"image_encoding_failed: {type(exc).__name__}: {exc}"
        payload = {
            "schema": FRAME_TRANSACTION_SCHEMA,
            "publication": publication,
            "image_frame_id": report.get("frame_id") if jpeg is not None and report is not None else None,
            "image_content_type": "image/jpeg" if jpeg is not None else None,
            "image_base64": base64.b64encode(jpeg).decode("ascii") if jpeg is not None else None,
            "image_error": image_error,
        }
        status, json_headers, encoded = json_response(payload, 200 if report is not None else 503)
        return status, {**json_headers, **headers}, encoded

    def _telemetry_unavailable(self, reason: str, status: int) -> Response:
        return json_response({
            "schema": TELEMETRY_SCHEMA, "ok": False,
            "status": "error" if status != 503 else "unavailable",
            "reason": reason, "record": None,
        }, status)

    def _telemetry_latest(self, query: dict[str, list[str]], body: bytes, headers: Mapping[str, str]) -> Response:
        latest = getattr(self.telemetry, "latest", None)
        if not callable(latest):
            return self._telemetry_unavailable("publisher_missing", 503)
        if query:
            return self._telemetry_unavailable("query_invalid", 400)
        try:
            payload = latest()
            if not isinstance(payload, dict):
                raise TypeError("host telemetry latest publication is not an object")
        except Exception:
            logger.exception("Unable to read host telemetry latest publication")
            return self._telemetry_unavailable("observer_error", 500)
        return json_response(payload, 200 if payload.get("ok") else 503)
