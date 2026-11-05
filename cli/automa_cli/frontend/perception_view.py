from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlparse
from urllib.request import urlopen

from PIL import Image

VIEW_SCHEMA = "automa_perception_view"
PUBLICATION_SCHEMA = "automa_perception_publication"
VIEW_RECORD_NAME = "perception_view.json"
MAX_BUFFERED_FRAMES = 8
NO_FRAME_ERROR = "no camera frame has been published yet"
_CONTENT_TYPE = "image/jpeg"


class PerceptionView:
    """Own perception publications and buffered camera images."""

    def __init__(self, *, host_name: str) -> None:
        self.host_name = host_name
        # Why there is no latest publication; the view's routes answer with it.
        self.unavailable_reason = NO_FRAME_ERROR
        self._lock = threading.RLock()
        self._frames: OrderedDict[str, tuple[bytes, str]] = OrderedDict()
        self._latest_frame: dict[str, Any] | None = None
        self._latest_perception_record: dict[str, Any] | None = None
        self._latest_frame_id: str | None = None

    def publish(self, *, frame_bytes: bytes, frame_record: dict[str, Any]) -> None:
        """Replace the latest JPEG and its findings together."""
        identity = _frame_identity(frame_record)
        frame_id = identity["frame_id"]
        # Checked before the lock so a rejected image does not replace the latest publication.
        with Image.open(BytesIO(frame_bytes)) as image:
            if image.format != "JPEG":
                raise ValueError("published frame must be a JPEG image")
            width_px, height_px = image.size
        published_at_ms = _timestamp_ms()
        frame = {
            "frame_id": frame_id,
            "frame_index": identity.get("frame_index"),
            "captured_at_ms": identity.get("captured_at_ms"),
            "published_at_ms": published_at_ms,
            "content_type": _CONTENT_TYPE,
            "width_px": width_px,
            "height_px": height_px,
            "url": f"/frame?v={quote(frame_id, safe='')}",
        }
        with self._lock:
            self._frames[frame_id] = (frame_bytes, _CONTENT_TYPE)
            self._frames.move_to_end(frame_id)
            if len(self._frames) > MAX_BUFFERED_FRAMES:
                self._frames.popitem(last=False)
            self._latest_frame = frame
            self._latest_frame_id = frame_id
            self._latest_perception_record = frame_record

    def invalidate_latest(self, reason: str | None = None) -> None:
        """An unavailable publication must not leave a usable latest frame; ``reason`` says why."""
        with self._lock:
            self.unavailable_reason = reason or NO_FRAME_ERROR
            self._latest_frame = None
            self._latest_frame_id = None
            self._latest_perception_record = None

    def health_payload(self) -> dict[str, Any]:
        with self._lock:
            return {
                "has_frame": self._latest_frame_id is not None,
                "latest_frame_id": self._latest_frame_id,
            }

    def latest_json(self) -> bytes | None:
        with self._lock:
            if self._latest_frame is None:
                return None
            payload = _publication_payload(
                host_name=self.host_name,
                frame=dict(self._latest_frame),
                perception_record=self._latest_perception_record,
                generated_at_ms=_timestamp_ms(),
            )
        return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")

    def frame(self, frame_id: str | None = None) -> tuple[bytes, str] | None:
        with self._lock:
            return self._frames.get(frame_id if frame_id is not None else self._latest_frame_id)


def get_perception_view_status(
    automation_dir: Path,
    *,
    timeout_s: float = 0.25,
    expected_run_id: str | None = None,
    expected_worker_pid: int | None = None,
) -> dict[str, Any]:
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
    generation_error = _view_generation_error(
        record,
        expected_run_id=expected_run_id,
        expected_worker_pid=expected_worker_pid,
    )
    if generation_error is not None:
        return {
            **record,
            "available": False,
            "status": "stale",
            "reason": generation_error,
        }
    try:
        with urlopen(urljoin(url, "api/health"), timeout=timeout_s) as response:
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
    generation_error = _view_generation_error(
        health,
        expected_run_id=expected_run_id,
        expected_worker_pid=expected_worker_pid,
    )
    if generation_error is not None:
        return {
            **record,
            **health,
            "available": False,
            "status": "stale",
            "reason": generation_error,
        }
    if not perception_view_ready(health):
        return {
            **record,
            **health,
            "available": False,
            "status": "warming",
            "reason": (
                "perception view has not published one correlated camera and "
                "perception frame"
            ),
        }
    return {
        **record,
        **health,
        "available": True,
        "status": "running",
        "reason": None,
    }


def perception_view_ready(payload: dict[str, Any]) -> bool:
    """True when the view has published one camera image and its findings."""

    return bool(
        payload.get("available")
        and payload.get("url")
        and payload.get("has_frame")
        and payload.get("latest_frame_id")
    )


def _view_generation_error(
    payload: dict[str, Any],
    *,
    expected_run_id: str | None,
    expected_worker_pid: int | None,
) -> str | None:
    if expected_run_id is not None and payload.get("run_id") != expected_run_id:
        return (
            "perception view belongs to a stale worker generation: "
            f"expected run_id={expected_run_id!r}, got {payload.get('run_id')!r}"
        )
    if (
        expected_worker_pid is not None
        and payload.get("worker_pid") != expected_worker_pid
    ):
        return (
            "perception view belongs to a stale worker process: "
            f"expected pid={expected_worker_pid}, got {payload.get('worker_pid')!r}"
        )
    return None


def _frame_identity(record: Any) -> dict[str, Any]:
    """Nested publication frame. Capture-only records use the same identity."""

    frame = record.get("frame") if isinstance(record, dict) else None
    frame_id = frame.get("frame_id") if isinstance(frame, dict) else None
    if not isinstance(frame_id, str) or not frame_id:
        raise ValueError("perception publication requires a nested frame with a frame_id")
    return frame


def _publication_payload(
    *,
    host_name: str,
    frame: dict[str, Any],
    perception_record: dict[str, Any] | None,
    generated_at_ms: int,
) -> dict[str, Any]:
    source = perception_record if isinstance(perception_record, dict) else {}
    perception = source.get("perception")
    perception = perception if isinstance(perception, dict) else None
    timing = source.get("timing") if isinstance(source.get("timing"), dict) else None
    identity = source.get("frame") if isinstance(source.get("frame"), dict) else {}
    return {
        "schema": PUBLICATION_SCHEMA,
        "host_name": host_name,
        "generated_at_ms": generated_at_ms,
        "result_age_ms": _nonnegative_difference(generated_at_ms, identity.get("completed_at_ms")),
        "frame": frame,
        "timing": timing,
        "duration_ms": source.get("duration_ms"),
        "memory": source.get("memory"),
        "perception": perception,
        "control": source.get("control"),
    }


def _nonnegative_difference(newer: Any, older: Any) -> int | None:
    if not isinstance(newer, (int, float)) or not isinstance(older, (int, float)):
        return None
    return max(0, int(newer - older))


def _is_loopback_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}


def _timestamp_ms() -> int:
    return int(time.time() * 1000)


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
