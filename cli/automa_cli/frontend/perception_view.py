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
_IMAGE_CONTENT_TYPES = {"image/jpeg", "image/png"}


class PerceptionView:
    """Own perception publications and buffered camera images."""

    def __init__(self, *, host_name: str) -> None:
        self.host_name = host_name
        # Why there is no latest pair; the view's routes answer with it.
        self.unavailable_reason = NO_FRAME_ERROR
        self._lock = threading.RLock()
        self._frames: OrderedDict[str, tuple[bytes, str]] = OrderedDict()
        self._retained_frame_ids: set[str] = set()
        self._latest_frame: dict[str, Any] | None = None
        self._latest_perception_record: dict[str, Any] | None = None
        self._latest_frame_id: str | None = None
        self._latest_perception_frame_id: str | None = None
        self._frame_published_at_ms: int | None = None
        self._perception_published_at_ms: int | None = None

    def publish_frame(self, *, frame_bytes: bytes, content_type: str, frame_record: dict[str, Any]) -> None:
        """Install image bytes already received for this publication."""
        self._publish_image(frame_bytes, content_type, frame_record, matched=False)

    def publish_perception(self, *, frame_record: dict[str, Any]) -> None:
        frame_id = _frame_identity(frame_record)["frame_id"]
        with self._lock:
            self._latest_perception_record = frame_record
            self._latest_perception_frame_id = frame_id
            self._perception_published_at_ms = _timestamp_ms()

    def publish_matched(self, *, frame_bytes: bytes, content_type: str, frame_record: dict[str, Any]) -> None:
        """Replace the image and findings together for an accepted transaction."""
        self._publish_image(frame_bytes, content_type, frame_record, matched=True)

    def _publish_image(
        self, frame_bytes: bytes, content_type: str, frame_record: dict[str, Any], *, matched: bool,
    ) -> None:
        # Dimensions are ready before the lock so publication stays a pair swap.
        if not isinstance(frame_bytes, bytes) or not frame_bytes:
            raise ValueError("published frame is empty")
        if content_type not in _IMAGE_CONTENT_TYPES:
            raise ValueError("published frame content type must be image/jpeg or image/png")
        identity = _frame_identity(frame_record)
        frame_id = identity["frame_id"]
        with Image.open(BytesIO(frame_bytes)) as image:
            width_px, height_px = int(image.size[0]), int(image.size[1])
        published_at_ms = _timestamp_ms()
        frame = {
            "frame_id": frame_id,
            "frame_index": identity.get("frame_index"),
            "captured_at_ms": identity.get("captured_at_ms"),
            "published_at_ms": published_at_ms,
            "content_type": content_type,
            "width_px": width_px,
            "height_px": height_px,
            "url": f"/frame?v={quote(frame_id, safe='')}",
        }
        with self._lock:
            if frame_id not in self._frames and len(self._frames) >= MAX_BUFFERED_FRAMES:
                evictable = next(
                    (
                        retained_id
                        for retained_id in self._frames
                        if retained_id not in self._retained_frame_ids
                    ),
                    None,
                )
                if evictable is None:
                    raise RuntimeError("perception frame capacity is retained in flight")
                del self._frames[evictable]
            self._frames[frame_id] = (frame_bytes, content_type)
            self._frames.move_to_end(frame_id)
            self._latest_frame = frame
            self._latest_frame_id = frame_id
            self._frame_published_at_ms = published_at_ms
            if matched:
                self._latest_perception_record = frame_record
                self._latest_perception_frame_id = frame_id
                self._perception_published_at_ms = published_at_ms

    def invalidate_latest(self, reason: str | None = None) -> None:
        """An unavailable publication must not leave a usable latest pair; ``reason`` says why."""
        with self._lock:
            self.unavailable_reason = reason or NO_FRAME_ERROR
            self._latest_frame = None
            self._latest_frame_id = None
            self._latest_perception_record = None
            self._latest_perception_frame_id = None

    def health_payload(self) -> dict[str, Any]:
        with self._lock:
            perception_id = self._latest_perception_frame_id
            source = (
                _frame_identity(self._latest_perception_record)
                if self._latest_perception_record is not None
                else {}
            )
            latest = self._latest_frame or {}
            has_perception = self._latest_perception_record is not None
            return {
                "has_frame": self._latest_frame_id is not None,
                "has_perception": has_perception,
                "latest_frame_id": self._latest_frame_id,
                "latest_perception_frame_id": perception_id,
                "perception_frame_buffered": (
                    perception_id is not None and perception_id in self._frames
                ),
                "capture_lag": (
                    _nonnegative_difference(
                        latest.get("frame_index"), source.get("frame_index")
                    )
                    if has_perception
                    else None
                ),
                "capture_lag_ms": (
                    _nonnegative_difference(
                        latest.get("captured_at_ms"), source.get("captured_at_ms")
                    )
                    if has_perception
                    else None
                ),
                "frame_published_at_ms": self._frame_published_at_ms,
                "perception_published_at_ms": self._perception_published_at_ms,
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

    def retain_frame(self, frame_id: str) -> bool:
        """Keep one buffered capture available while its queued cycle runs."""

        with self._lock:
            if frame_id not in self._frames:
                return False
            self._retained_frame_ids.add(frame_id)
            return True

    def release_frame(self, frame_id: str) -> None:
        """Release a queued capture without changing the bounded cache size."""

        with self._lock:
            self._retained_frame_ids.discard(frame_id)


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
        image_missing = (
            health.get("has_perception")
            and health.get("latest_perception_frame_id")
            and not health.get("perception_frame_buffered")
        )
        return {
            **record,
            **health,
            "available": False,
            "status": "warming",
            "reason": (
                "perception view completed a result whose camera image "
                "is no longer buffered"
                if image_missing
                else (
                    "perception view has not published one correlated camera and "
                    "perception frame"
                )
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
    """True when a completed result and its camera image are both available.

    A newer capture does not withdraw readiness. Its distance from the
    completed result is ``capture_lag`` on the view payload.
    """

    return bool(
        payload.get("available")
        and payload.get("url")
        and payload.get("has_perception")
        and payload.get("latest_perception_frame_id")
        and payload.get("perception_frame_buffered")
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
    overlay = _overlay_payload(frame=frame, perception_record=perception_record, now_ms=generated_at_ms)
    return {
        "schema": PUBLICATION_SCHEMA,
        "host_name": host_name,
        "generated_at_ms": generated_at_ms,
        "frame": frame,
        "overlay": overlay,
        "timing": timing,
        "duration_ms": source.get("duration_ms"),
        "memory": source.get("memory"),
        "perception": perception,
        "control": source.get("control"),
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

    source = _frame_identity(perception_record)
    source_frame_id = source.get("frame_id")
    source_frame_index = source.get("frame_index")
    current_frame_index = frame.get("frame_index")
    source_captured_at_ms = source.get("captured_at_ms")
    current_captured_at_ms = frame.get("captured_at_ms")
    completed_at_ms = source.get("completed_at_ms")
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
