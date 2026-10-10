"""The HTTP contract every vehicle runtime serves.

``RuntimeRoutes.handle`` maps one request onto a ``FrameLoop`` and its cycle
host and returns ``(status, headers, body)``. Transports only move bytes:
the PiCar serves this table from Donkey's Tornado app, the Chase host from a
stdlib server. The CLI reads every vehicle through the same routes.
"""
from __future__ import annotations

import json
import logging
import threading
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from autonomy.runtime.frame_loop import (
    CAMERA_LATEST_FRAME_PATH,
    CAMERA_LATEST_JSON_PATH,
    DECISION_LATEST_PATH,
    FrameLoop,
    LATEST_FRAME_PATH,
    LATEST_JSON_PATH,
)
from autonomy.runtime.session import RunConfiguration

logger = logging.getLogger(__name__)

STATUS_PATH = "/autonomy/status"
RUNTIME_PATH = "/autonomy/runtime"
PLUGINS_PATH = "/api/plugins"
MEMORY_RESET_PATH = "/autonomy/memory/reset"
TELEMETRY_LATEST_PATH = "/autonomy/telemetry/latest"
TELEMETRY_RECORDS_PATH = "/autonomy/telemetry/records"
MEMORY_RESET_SCHEMA = "automa_memory_reset_v0"
TELEMETRY_SCHEMA = "automa_host_boundary_telemetry_v0"
RESTART_DELAY_S = 0.2

Response = tuple[int, dict[str, str], bytes]


def json_response(payload: dict[str, Any], status: int = 200) -> Response:
    body = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return status, {"Content-Type": "application/json", "Cache-Control": "no-store"}, body


class RuntimeRoutes:
    """Serve one runtime's status, lifecycle, catalog, and publications.

    ``telemetry`` is the optional host-boundary observer (``latest`` and
    ``records``). ``on_restart`` replaces the process after a restart
    response is sent; the service manager starts a fresh host.
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
        self._routes: dict[tuple[str, str], Callable[[dict[str, list[str]], bytes], Response]] = {
            ("GET", STATUS_PATH): self._status,
            ("GET", RUNTIME_PATH): self._runtime,
            ("POST", RUNTIME_PATH): self._runtime_command,
            ("GET", PLUGINS_PATH): self._catalog,
            ("POST", PLUGINS_PATH): self._catalog,
            ("GET", LATEST_JSON_PATH): self._observation,
            ("GET", LATEST_FRAME_PATH): self._observation_frame,
            ("GET", CAMERA_LATEST_JSON_PATH): self._camera,
            ("GET", CAMERA_LATEST_FRAME_PATH): self._camera_frame,
            ("GET", DECISION_LATEST_PATH): self._decision,
            ("POST", MEMORY_RESET_PATH): self._memory_reset,
            ("GET", TELEMETRY_LATEST_PATH): self._telemetry_latest,
            ("GET", TELEMETRY_RECORDS_PATH): self._telemetry_records,
        }

    def handles(self, path: str) -> bool:
        return any(route_path == urlsplit(path).path for _, route_path in self._routes)

    def handle(self, method: str, target: str, body: bytes = b"") -> Response:
        """Answer one request; ``target`` is the path with its query string."""

        parts = urlsplit(target)
        path = parts.path.rstrip("/") or "/"
        method = "GET" if method.upper() == "HEAD" else method.upper()
        route = self._routes.get((method, path))
        if route is None:
            if any(route_path == path for _, route_path in self._routes):
                return json_response({"ok": False, "error": f"{method} not allowed on {path}"}, 405)
            return json_response({"ok": False, "error": f"no runtime route {path}"}, 404)
        try:
            query = parse_qs(parts.query, keep_blank_values=True, strict_parsing=False)
            return route(query, body or b"")
        except Exception as exc:  # noqa: BLE001 - one bad request must not end the host
            logger.exception("Runtime route %s %s failed", method, path)
            return json_response({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 500)

    def status_payload(self) -> dict[str, Any]:
        session = self.host.session_status()
        return {
            "ok": True,
            "host_run_id": self.loop.run_id,
            "runtime": self.loop.runtime,
            "mode": session["execution"]["mode"],
            "session": session,
            "autonomy": self.host.status(),
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

    def _status(self, query: dict[str, list[str]], body: bytes) -> Response:
        return json_response(self.status_payload())

    def _runtime(self, query: dict[str, list[str]], body: bytes) -> Response:
        return json_response({
            "ok": True,
            "host_run_id": self.loop.run_id,
            "session": self.host.session_status(),
        })

    def _runtime_command(self, query: dict[str, list[str]], body: bytes) -> Response:
        try:
            data = self._json_body(body)
            command = data.get("command")
            if command == "run":
                configuration = data.get("configuration") or {}
                if not isinstance(configuration, dict):
                    raise ValueError("configuration must be an object")
                self.loop.start(RunConfiguration(**configuration), record=bool(data.get("record", False)))
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
            return json_response({"ok": False, "error": str(exc)}, 400)
        response = self._runtime(query, body)
        if command == "restart":
            if self.on_restart is None:
                return json_response({"ok": False, "error": "this runtime cannot restart itself"}, 400)
            threading.Timer(RESTART_DELAY_S, self.on_restart).start()
        return response

    def _catalog(self, query: dict[str, list[str]], body: bytes) -> Response:
        try:
            payload = self._json_body(body) if body else None
        except ValueError as exc:
            return json_response({"ok": False, "error": str(exc)}, 400)
        # Uploads last for this host run; the id shows when a restart cleared them.
        result = {**self.host.plugin_catalog(payload), "host_run_id": self.loop.run_id}
        return json_response(result, 200 if result.get("ok") else 400)

    def _observation(self, query: dict[str, list[str]], body: bytes) -> Response:
        payload = self.loop.publish_latest()
        return json_response(payload, 503 if payload.get("health") == "absent" else 200)

    def _camera(self, query: dict[str, list[str]], body: bytes) -> Response:
        payload = self.loop.publish_latest_camera()
        unavailable = payload.get("health") in {"absent", "unavailable"} and not payload.get("ok")
        return json_response(payload, 503 if unavailable else 200)

    def _observation_frame(self, query: dict[str, list[str]], body: bytes) -> Response:
        return self._frame(*self.loop.publish_latest_frame_jpeg())

    def _camera_frame(self, query: dict[str, list[str]], body: bytes) -> Response:
        return self._frame(*self.loop.publish_latest_camera_jpeg())

    @staticmethod
    def _frame(jpeg: bytes | None, meta: dict[str, Any]) -> Response:
        frame = meta.get("frame") if isinstance(meta.get("frame"), dict) else {}
        headers = {"Cache-Control": "no-store"}
        for header, key in (
            ("X-Frame-Id", "frame_id"),
            ("X-Frame-Index", "frame_index"),
            ("X-Captured-At-Ms", "captured_at_ms"),
            ("X-Completed-At-Ms", "completed_at_ms"),
        ):
            if frame.get(key) is not None:
                headers[header] = str(frame[key])
        if meta.get("health") is not None:
            headers["X-Observation-Health"] = str(meta["health"])
        if jpeg is None:
            status, json_headers, body = json_response(meta, 503)
            return status, {**json_headers, **headers}, body
        return 200, {**headers, "Content-Type": "image/jpeg", "Content-Length": str(len(jpeg))}, jpeg

    def _decision(self, query: dict[str, list[str]], body: bytes) -> Response:
        payload = self.loop.publish_decision_latest()
        return json_response(payload, 200 if payload.get("status") == "ready" else 503)

    def _memory_reset(self, query: dict[str, list[str]], body: bytes) -> Response:
        payload = {"schema": MEMORY_RESET_SCHEMA, **self.loop.reset_memory()}
        autonomy = self.host.status()
        payload["autonomy"] = autonomy
        steps = autonomy.get("steps")
        if isinstance(steps, dict) and isinstance(steps.get("memory"), dict):
            payload.setdefault("memory", steps["memory"])
        status = 200 if payload.get("ok") else 409
        if payload.get("status") == "absent":
            status = 503
        elif payload.get("status") == "error":
            status = 500
        return json_response(payload, status)

    def _telemetry_unavailable(self, field: str, reason: str, status: int) -> Response:
        return json_response({
            "schema": TELEMETRY_SCHEMA, "ok": False,
            "status": "error" if status != 503 else "unavailable",
            "reason": reason, field: None if field == "record" else [],
        }, status)

    def _telemetry_latest(self, query: dict[str, list[str]], body: bytes) -> Response:
        latest = getattr(self.telemetry, "latest", None)
        if not callable(latest):
            return self._telemetry_unavailable("record", "publisher_missing", 503)
        if query:
            return self._telemetry_unavailable("record", "query_invalid", 400)
        try:
            payload = latest()
            if not isinstance(payload, dict):
                raise TypeError("host telemetry latest publication is not an object")
        except Exception:
            logger.exception("Unable to read host telemetry latest publication")
            return self._telemetry_unavailable("record", "observer_error", 500)
        return json_response(payload, 200 if payload.get("ok") else 503)

    def _telemetry_records(self, query: dict[str, list[str]], body: bytes) -> Response:
        records = getattr(self.telemetry, "records", None)
        if not callable(records):
            return self._telemetry_unavailable("records", "publisher_missing", 503)
        if set(query) != {"after_sequence", "limit"} or any(
            len(values) != 1 or not values[0].isascii() for values in query.values()
        ):
            return self._telemetry_unavailable("records", "query_invalid", 400)
        try:
            payload = records(after_sequence=query["after_sequence"][0], limit=query["limit"][0])
            if not isinstance(payload, dict):
                raise TypeError("host telemetry history publication is not an object")
        except Exception:
            logger.exception("Unable to read host telemetry history")
            return self._telemetry_unavailable("records", "observer_error", 500)
        status = 200 if payload.get("ok") else 503
        if payload.get("reason") == "query_invalid":
            status = 400
        return json_response(payload, status)
