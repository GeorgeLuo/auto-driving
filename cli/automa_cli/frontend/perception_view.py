from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse
from urllib.request import urlopen

from PIL import Image

from autonomy.runtime.frame_loop import FRAME_PUBLICATION_SCHEMA, FramePublication

from .decision_view import report_evidence

VIEW_SCHEMA = "automa_perception_view"
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
        # Image metadata, the publication, telemetry, evidence, and the image id.
        self._latest: tuple[dict[str, object], FramePublication, object, object, str] | None = None

    def publish(
        self,
        *,
        frame_bytes: bytes | None,
        publication: FramePublication,
        host_telemetry: object = None,
    ) -> None:
        """Replace the latest report. The JPEG is the display image when encoding produced one."""
        # Copy at publish so a later edit of the caller's report stays off the page.
        stored = deepcopy(publication)
        report = stored.report
        if report is None:
            raise ValueError("frame publication requires report.frame_id")
        image_id = json.dumps(
            [report.run_id, report.generation_id, report.frame_id], separators=(",", ":"),
        )
        width_px = height_px = None
        if frame_bytes:
            try:
                with Image.open(BytesIO(frame_bytes)) as image:
                    if image.format != "JPEG":
                        raise ValueError("published frame must be a JPEG image")
                    width_px, height_px = image.size
            except (OSError, ValueError):
                frame_bytes = None
        frame: dict[str, object] = {
            "frame_id": report.frame_id,
            "frame_index": report.frame_index,
            "captured_at_ms": report.timestamp_ms,
            "published_at_ms": report.published_at_ms or _timestamp_ms(),
            "content_type": _CONTENT_TYPE if frame_bytes else None,
            "width_px": width_px,
            "height_px": height_px,
            "url": f"/frame?v={quote(image_id, safe='')}" if frame_bytes else None,
        }
        # Projected once per publish; every read of the latest frame serves it.
        evidence = report_evidence(report)
        with self._lock:
            if frame_bytes:
                self._frames[image_id] = (frame_bytes, _CONTENT_TYPE)
                self._frames.move_to_end(image_id)
                if len(self._frames) > MAX_BUFFERED_FRAMES:
                    self._frames.popitem(last=False)
            self._latest = frame, stored, host_telemetry, evidence, image_id

    def invalidate_latest(self, reason: str | None = None) -> None:
        """An unavailable publication must not leave a usable latest frame; ``reason`` says why."""
        with self._lock:
            self.unavailable_reason = reason or NO_FRAME_ERROR
            self._latest = None

    def health_payload(self) -> dict[str, object]:
        with self._lock:
            return {"latest_frame_id": self._latest_frame_id()}

    def latest_json(self) -> bytes | None:
        with self._lock:
            if self._latest is None:
                return None
            frame, publication, telemetry, evidence, _image_id = self._latest
        payload = _publication_payload(
            host_name=self.host_name,
            image=frame,
            publication=publication,
            host_telemetry=telemetry,
            evidence=evidence,
            generated_at_ms=_timestamp_ms(),
        )
        return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")

    def frame(self, frame_id: str | None = None) -> tuple[bytes, str] | None:
        with self._lock:
            image_id = frame_id if frame_id is not None else (None if self._latest is None else self._latest[4])
            return self._frames.get(image_id)

    def _latest_frame_id(self) -> str | None:
        return None if self._latest is None else self._latest[0]["frame_id"]


def get_perception_view_status(
    automation_dir: Path,
    *,
    timeout_s: float = 0.25,
) -> dict[str, object]:
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
        with urlopen(urljoin(url, "api/health"), timeout=timeout_s) as response:
            health: object = json.loads(response.read().decode("utf-8"))
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
    # A view that ended without a trace can leave its port to another host's view.
    if health.get("host_name") != record.get("host_name"):
        return {
            **record,
            "available": False,
            "status": "unavailable",
            "reason": f"the recorded view's port now serves host {health.get('host_name')!r}",
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


def perception_view_ready(payload: dict[str, object]) -> bool:
    """True when the view has published one camera image and its findings."""

    return bool(payload.get("available") and payload.get("url") and payload.get("latest_frame_id"))


def _publication_payload(
    *,
    host_name: str,
    image: dict[str, object],
    publication: FramePublication,
    host_telemetry: object,
    evidence: object,
    generated_at_ms: int,
) -> dict[str, object]:
    report = publication.report
    published_at_ms = None if report is None else report.published_at_ms
    age = _nonnegative_difference(generated_at_ms, published_at_ms)
    health = publication.health
    if age is not None and age > publication.stale_after_ms:
        health = "stale"
    return {
        "schema": FRAME_PUBLICATION_SCHEMA,
        "host_name": host_name,
        "health": health,
        "ok": report is not None,
        "read_at_ms": generated_at_ms,
        "result_age_ms": age,
        "stale_after_ms": publication.stale_after_ms,
        "report": None if report is None else report.to_dict(),
        "image": image,
        "host_telemetry": host_telemetry,
        "evidence": evidence,
    }


def _nonnegative_difference(newer: int, older: int | None) -> int | None:
    if older is None:
        return None
    return max(0, newer - older)


def _is_loopback_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}


def _timestamp_ms() -> int:
    return int(time.time() * 1000)


def _read_json(path: Path) -> object | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
