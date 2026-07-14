from __future__ import annotations

import json
import mimetypes
import os
import threading
import time
import zlib
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urljoin, urlparse
from urllib.request import urlopen

from PIL import Image


VIEW_SCHEMA = "automa_perception_view_v1"
PUBLICATION_SCHEMA = "automa_perception_publication_v1"
VIEW_RECORD_NAME = "perception_view.json"
VIEW_HOST = "127.0.0.1"
VIEW_HTML_PATH = Path(__file__).with_name("perception_view.html")
MAX_BUFFERED_FRAMES = 8


class PerceptionViewServer:
    """Publish live frames independently from slower perception results."""

    def __init__(
        self,
        *,
        vehicle_id: str,
        automation_dir: Path,
        host: str = VIEW_HOST,
        port: int | None = None,
    ) -> None:
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("perception view must bind to a loopback address")
        self.vehicle_id = vehicle_id
        self.automation_dir = automation_dir
        self.host = host
        self.preferred_port = _vehicle_view_port(vehicle_id) if port is None else int(port)
        self.record_path = automation_dir / VIEW_RECORD_NAME
        self._lock = threading.Lock()
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._frame_bytes: bytes | None = None
        self._frame_content_type = "application/octet-stream"
        self._frames: OrderedDict[str, tuple[bytes, str]] = OrderedDict()
        self._latest_frame: dict[str, Any] | None = None
        self._latest_perception_record: dict[str, Any] | None = None
        self._latest_frame_id: str | None = None
        self._latest_perception_frame_id: str | None = None
        self._frame_published_at_ms: int | None = None
        self._perception_published_at_ms: int | None = None
        self._started_at_ms: int | None = None

    @property
    def url(self) -> str | None:
        if self._httpd is None:
            return None
        host, port = self._httpd.server_address[:2]
        display_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
        return f"http://{display_host}:{port}/"

    def start(self) -> "PerceptionViewServer":
        if self._httpd is not None:
            return self
        self.automation_dir.mkdir(parents=True, exist_ok=True)
        try:
            httpd = _PerceptionHttpServer((self.host, self.preferred_port), _PerceptionViewHandler)
        except OSError:
            if self.preferred_port == 0:
                raise
            httpd = _PerceptionHttpServer((self.host, 0), _PerceptionViewHandler)
        httpd.publisher = self
        self._httpd = httpd
        self._started_at_ms = _timestamp_ms()
        self._thread = threading.Thread(
            target=httpd.serve_forever,
            name=f"automa-perception-view-{self.vehicle_id}",
            daemon=True,
        )
        self._thread.start()
        _write_json(self.record_path, self.describe())
        return self

    def publish_frame(self, *, frame_path: Path, frame_record: dict[str, Any]) -> None:
        frame_bytes = frame_path.read_bytes()
        if not frame_bytes:
            raise ValueError(f"published frame is empty: {frame_path}")
        content_type = _frame_content_type(frame_path, frame_record)
        published_at_ms = _timestamp_ms()
        frame_id = str(frame_record.get("frame_id") or "unknown")
        width_px, height_px = _image_dimensions(frame_path)
        frame = _frame_payload(
            frame_record=frame_record,
            content_type=content_type,
            published_at_ms=published_at_ms,
            width_px=width_px,
            height_px=height_px,
        )
        with self._lock:
            self._frame_bytes = frame_bytes
            self._frame_content_type = content_type
            self._frames[frame_id] = (frame_bytes, content_type)
            self._frames.move_to_end(frame_id)
            while len(self._frames) > MAX_BUFFERED_FRAMES:
                self._frames.popitem(last=False)
            self._latest_frame = frame
            self._latest_frame_id = frame_id
            self._frame_published_at_ms = published_at_ms

    def publish_perception(self, *, frame_record: dict[str, Any]) -> None:
        frame_id = str(frame_record.get("frame_id") or "unknown")
        with self._lock:
            self._latest_perception_record = frame_record
            self._latest_perception_frame_id = frame_id
            self._perception_published_at_ms = _timestamp_ms()

    def describe(self, *, status: str = "running") -> dict[str, Any]:
        return {
            "schema": VIEW_SCHEMA,
            "vehicle_id": self.vehicle_id,
            "status": status,
            "available": status == "running" and self._httpd is not None,
            "url": self.url,
            "host": self.host,
            "port": self._httpd.server_address[1] if self._httpd is not None else None,
            "pid": os.getpid(),
            "started_at_ms": self._started_at_ms,
            "record_path": str(self.record_path),
        }

    def health_payload(self) -> dict[str, Any]:
        with self._lock:
            return {
                **self.describe(),
                "has_frame": self._frame_bytes is not None,
                "has_perception": self._latest_perception_record is not None,
                "latest_frame_id": self._latest_frame_id,
                "latest_perception_frame_id": self._latest_perception_frame_id,
                "frame_published_at_ms": self._frame_published_at_ms,
                "perception_published_at_ms": self._perception_published_at_ms,
            }

    def latest_json(self) -> bytes | None:
        with self._lock:
            if self._latest_frame is None:
                return None
            payload = _publication_payload(
                vehicle_id=self.vehicle_id,
                frame=dict(self._latest_frame),
                perception_record=self._latest_perception_record,
                generated_at_ms=_timestamp_ms(),
            )
        return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")

    def frame(self, frame_id: str | None = None) -> tuple[bytes, str] | None:
        with self._lock:
            if frame_id is not None:
                return self._frames.get(frame_id)
            if self._frame_bytes is None:
                return None
            return self._frame_bytes, self._frame_content_type

    def stop(self) -> None:
        httpd = self._httpd
        thread = self._thread
        if httpd is None:
            return
        httpd.shutdown()
        httpd.server_close()
        if thread is not None:
            thread.join(timeout=1.0)
        _write_json(self.record_path, self.describe(status="stopped"))
        self._httpd = None
        self._thread = None


class _PerceptionHttpServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    publisher: PerceptionViewServer


class _PerceptionViewHandler(BaseHTTPRequestHandler):
    server: _PerceptionHttpServer

    def do_GET(self) -> None:
        self._handle_request(include_body=True)

    def do_HEAD(self) -> None:
        self._handle_request(include_body=False)

    def _handle_request(self, *, include_body: bool) -> None:
        request = urlparse(self.path)
        route = request.path
        if route == "/favicon.ico":
            self._send(204, b"", "image/x-icon", include_body=False)
            return
        if route in {"/", "/index.html"}:
            try:
                body = VIEW_HTML_PATH.read_bytes()
            except OSError as exc:
                self._send_json(500, {"error": str(exc)}, include_body=include_body)
                return
            self._send(200, body, "text/html; charset=utf-8", include_body=include_body)
            return
        if route == "/api/health":
            self._send_json(
                200,
                self.server.publisher.health_payload(),
                include_body=include_body,
            )
            return
        if route == "/api/latest":
            body = self.server.publisher.latest_json()
            if body is None:
                self._send_json(
                    503,
                    {"error": "no camera frame has been published yet"},
                    include_body=include_body,
                )
                return
            self._send(
                200,
                body,
                "application/json; charset=utf-8",
                include_body=include_body,
            )
            return
        if route == "/frame":
            requested_frame_id = parse_qs(request.query).get("v", [None])[0]
            frame = self.server.publisher.frame(requested_frame_id)
            if frame is None:
                if requested_frame_id is not None:
                    self._send_json(
                        404,
                        {"error": "requested perception frame is no longer available"},
                        include_body=include_body,
                    )
                    return
                self._send_json(
                    503,
                    {"error": "no camera frame has been published yet"},
                    include_body=include_body,
                )
                return
            body, content_type = frame
            self._send(200, body, content_type, include_body=include_body)
            return
        self._send_json(404, {"error": "not found"}, include_body=include_body)

    def _send_json(
        self,
        status: int,
        payload: dict[str, Any],
        *,
        include_body: bool = True,
    ) -> None:
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        self._send(
            status,
            body,
            "application/json; charset=utf-8",
            include_body=include_body,
        )

    def _send(
        self,
        status: int,
        body: bytes,
        content_type: str,
        *,
        include_body: bool = True,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self'; connect-src 'self'; "
            "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'",
        )
        self.end_headers()
        if include_body:
            self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return None


def get_perception_view_status(automation_dir: Path, *, timeout_s: float = 0.25) -> dict[str, Any]:
    record_path = automation_dir / VIEW_RECORD_NAME
    record = _read_json(record_path)
    if not isinstance(record, dict):
        return {
            "schema": VIEW_SCHEMA,
            "available": False,
            "status": "unavailable",
            "url": None,
            "reason": "automation has not published a perception view",
            "record_path": str(record_path),
        }
    url = record.get("url") if isinstance(record.get("url"), str) else None
    if url is None or not _is_loopback_url(url):
        return {
            **record,
            "available": False,
            "status": "unavailable",
            "reason": "perception view record has no valid loopback URL",
        }
    try:
        with urlopen(urljoin(url, "api/health"), timeout=max(0.05, timeout_s)) as response:
            health = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {
            **record,
            "available": False,
            "status": "unavailable",
            "reason": f"perception view is not responding: {exc}",
        }
    if not isinstance(health, dict) or health.get("schema") != VIEW_SCHEMA:
        return {
            **record,
            "available": False,
            "status": "unavailable",
            "reason": "perception view returned an invalid health response",
        }
    return {
        **record,
        **health,
        "available": True,
        "status": "running",
        "reason": None,
    }


def _publication_payload(
    *,
    vehicle_id: str,
    frame: dict[str, Any],
    perception_record: dict[str, Any] | None,
    generated_at_ms: int,
) -> dict[str, Any]:
    source = perception_record or {}
    perception = source.get("perception")
    perception = perception if isinstance(perception, dict) else None
    overlay = _overlay_payload(frame=frame, perception_record=perception_record, now_ms=generated_at_ms)
    return {
        "schema": PUBLICATION_SCHEMA,
        "vehicle_id": vehicle_id,
        "generated_at_ms": generated_at_ms,
        "frame": frame,
        "overlay": overlay,
        "cycle": {
            "cycle_duration_ms": source.get("cycle_duration_ms"),
            "perception_duration_ms": source.get("perception_duration_ms"),
            "control_source": source.get("control_source"),
            "control_application": source.get("control_application"),
            "action_policy": source.get("action_policy"),
        },
        "perception": perception,
        "sensor_snapshot": source.get("sensor_snapshot"),
        "observation": source.get("observation"),
        "control": source.get("control"),
        "engine": source.get("engine"),
    }


def _frame_payload(
    *,
    frame_record: dict[str, Any],
    content_type: str,
    published_at_ms: int,
    width_px: int,
    height_px: int,
) -> dict[str, Any]:
    frame_id = frame_record.get("frame_id")
    return {
        "frame_id": frame_id,
        "frame_index": frame_record.get("frame_index"),
        "captured_at_ms": frame_record.get("captured_at_ms"),
        "published_at_ms": published_at_ms,
        "content_type": content_type,
        "width_px": width_px,
        "height_px": height_px,
        "url": f"/frame?v={quote(str(frame_id or 'unknown'), safe='')}",
    }


def _overlay_payload(
    *,
    frame: dict[str, Any],
    perception_record: dict[str, Any] | None,
    now_ms: int,
) -> dict[str, Any]:
    if perception_record is None:
        return {
            "status": "pending",
            "source_frame_id": None,
            "source_frame_index": None,
            "source_captured_at_ms": None,
            "perception_completed_at_ms": None,
            "frame_lag": None,
            "frame_lag_ms": None,
            "result_age_ms": None,
        }

    source_frame_id = perception_record.get("frame_id")
    source_frame_index = perception_record.get("frame_index")
    current_frame_index = frame.get("frame_index")
    source_captured_at_ms = perception_record.get("captured_at_ms")
    current_captured_at_ms = frame.get("captured_at_ms")
    completed_at_ms = perception_record.get("perception_completed_at_ms")
    frame_lag = _nonnegative_difference(current_frame_index, source_frame_index)
    frame_lag_ms = _nonnegative_difference(current_captured_at_ms, source_captured_at_ms)
    result_age_ms = _nonnegative_difference(now_ms, completed_at_ms)
    return {
        "status": "current" if source_frame_id == frame.get("frame_id") else "stale",
        "source_frame_id": source_frame_id,
        "source_frame_index": source_frame_index,
        "source_captured_at_ms": source_captured_at_ms,
        "perception_completed_at_ms": completed_at_ms,
        "frame_lag": frame_lag,
        "frame_lag_ms": frame_lag_ms,
        "result_age_ms": result_age_ms,
    }


def _nonnegative_difference(newer: Any, older: Any) -> int | None:
    if not isinstance(newer, (int, float)) or not isinstance(older, (int, float)):
        return None
    return max(0, int(newer - older))


def _image_dimensions(frame_path: Path) -> tuple[int, int]:
    with Image.open(frame_path) as image:
        width, height = image.size
    return int(width), int(height)


def _frame_content_type(frame_path: Path, frame_record: dict[str, Any]) -> str:
    snapshot = frame_record.get("sensor_snapshot")
    if isinstance(snapshot, dict):
        readings = snapshot.get("readings")
        reading = readings.get("front_camera") if isinstance(readings, dict) else None
        metadata = reading.get("metadata") if isinstance(reading, dict) else None
        content_type = metadata.get("content_type") if isinstance(metadata, dict) else None
        if isinstance(content_type, str) and content_type.startswith("image/"):
            return content_type
    guessed, _ = mimetypes.guess_type(frame_path.name)
    return guessed if isinstance(guessed, str) and guessed.startswith("image/") else "application/octet-stream"


def _vehicle_view_port(vehicle_id: str) -> int:
    return 8500 + (zlib.crc32(vehicle_id.encode("utf-8")) % 500)


def _is_loopback_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}


def _timestamp_ms() -> int:
    return int(time.time() * 1000)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
