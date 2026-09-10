"""Generation-bound live decision view support for Automa (M006 D2).

The decision view is deliberately a projection at the existing perception-view
boundary.  It consumes an already accepted ``vehicle_decision_stream_frame_v0``
and the bytes which entered the existing capture publication.  It does not run
the decision engine, resolve arbitrary files, or create a second server.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import threading
import time
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, unquote, urlencode, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from PIL import Image, UnidentifiedImageError

from autonomy.decision import canonical_json_utf8

from .decision import (
    DECISION_STREAM_MAX_AGE_MS,
    DecisionSurfaceError,
    accept_decision_stream_frame,
    is_pid_alive,
    _read_surface_activation,
)


DECISION_VIEW_SCHEMA = "automa_decision_view_v1"
DECISION_VIEW_ID = "decision-combined-v0"
DECISION_VIEW_PATH = "/decision"
DECISION_API_PATH = "/api/decision/latest"
DECISION_IMAGE_PATH = "/api/decision/images/"

MAX_IMAGES = 64
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_IMAGE_BYTES = 32 * 1024 * 1024
MAX_PIXELS_PER_IMAGE = 16_000_000
MAX_METADATA_BYTES = 8 * 1024 * 1024
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_DECISION_FILE_BYTES = 8 * 1024 * 1024
MAX_RECORD_BYTES = 1 * 1024 * 1024
MAX_POLYGON_VERTICES = 256
# Keep identity/timestamp values within the exact integer range that the
# existing shadow identity contract can represent without JSON/JavaScript
# precision loss.
MAX_SAFE_INT = 9_007_199_254_740_991
GENERATION_RE = re.compile(r"[0-9a-f]{64}\Z")
IMAGE_ID_RE = GENERATION_RE
IMAGE_CONTENT_TYPES = frozenset({"image/png", "image/jpeg"})
HOST_OBSERVATION_SCHEMA = "automa_host_observation_v1"


class DecisionViewHTTPError(Exception):
    """An explicit D2 route refusal with an HTTP status and stable reason."""

    def __init__(
        self,
        status_code: int,
        reason: str,
        message: str,
        *,
        identity: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = int(status_code)
        self.reason = reason
        self.message = message
        self.identity = deepcopy(identity) if isinstance(identity, dict) else None


@dataclass(frozen=True)
class _ImageRecord:
    frame_id: str
    frame_index: int
    captured_at_ms: int
    content_type: str
    data: bytes
    width_px: int
    height_px: int
    sensor_snapshot: dict[str, Any] | None
    environment_frame: dict[str, Any] | None
    observation_id: str | None = None
    image_id: str | None = None
    sha256: str | None = None
    association_error: str | None = None


@dataclass(frozen=True)
class _ObservationArchive:
    frame_id: str
    frame_index: int | None
    captured_at_ms: int | None
    observation_id: str
    observation: dict[str, Any]
    association_error: str | None = None
    environment_frame: dict[str, Any] | None = None


@dataclass(frozen=True)
class _StoreSnapshot:
    entries: dict[str, _ImageRecord]
    archives: dict[tuple[str, str], _ObservationArchive]
    image_count: int
    image_bytes: int
    metadata_bytes: int
    retention_refusals: int
    size_refusals: int


def timestamp_ms() -> int:
    return int(time.time() * 1000)


def _canonical(value: Any) -> bytes:
    """Return the repository's sorted, compact, finite JSON bytes."""

    return canonical_json_utf8(value)


def _sha256(value: bytes) -> str:
    """Return the complete lower-case SHA-256 digest for exact bytes."""

    return hashlib.sha256(value).hexdigest()


def _is_nonnegative_int(value: object) -> bool:
    return type(value) is int and 0 <= value <= MAX_SAFE_INT


def _is_finite_number(value: object) -> bool:
    if type(value) is int:
        return -MAX_SAFE_INT <= value <= MAX_SAFE_INT
    return type(value) is float and math.isfinite(value)


def _copy_json(value: Any) -> Any:
    """Deep-copy a value through strict canonical JSON serialization."""

    return json.loads(_canonical(value).decode("utf-8"))


def activation_sha256(activation: dict[str, Any]) -> str:
    """Hash only the immutable engine/configuration activation identity."""

    if not isinstance(activation, dict):
        raise ValueError("decision activation must be a JSON object")
    decision = activation.get("decision")
    if not isinstance(decision, dict):
        raise ValueError("decision activation has no decision section")
    activated_at_ms = activation.get("activated_at_ms")
    if not _is_nonnegative_int(activated_at_ms):
        raise ValueError(
            "decision activation activated_at_ms must be a non-negative safe int"
        )
    engine_id = decision.get("engine_id")
    if type(engine_id) is not str or not engine_id.strip():
        raise ValueError("decision activation engine_id must be a non-empty string")
    engine_spec = decision.get("engine_spec")
    if type(engine_spec) is not str or not engine_spec.strip():
        raise ValueError("decision activation engine_spec must be a non-empty string")
    engine_config = decision.get("engine_config")
    if not isinstance(engine_config, dict):
        raise ValueError("decision activation engine_config must be an object")
    payload = {
        "engine_id": engine_id,
        "engine_spec": engine_spec,
        "engine_config": _copy_json(engine_config),
        "activated_at_ms": activated_at_ms,
    }
    return _sha256(_canonical(payload))


def identity_for_activation(
    *,
    vehicle_id: str,
    run_id: str,
    worker_pid: int,
    activation: dict[str, Any],
) -> dict[str, Any]:
    if type(vehicle_id) is not str or not vehicle_id:
        raise ValueError("vehicle_id must be a non-empty string")
    if type(run_id) is not str or not run_id:
        raise ValueError("run_id must be a non-empty string")
    if not _is_nonnegative_int(worker_pid) or worker_pid <= 0:
        raise ValueError("worker_pid must be a positive non-bool safe int")
    activation_hash = activation_sha256(activation)
    decision = activation["decision"]
    activated_at_ms = activation["activated_at_ms"]
    engine_id = decision["engine_id"]
    return {
        "vehicle_id": vehicle_id,
        "run_id": run_id,
        "worker_pid": worker_pid,
        "activation_engine_id": engine_id,
        "activation_activated_at_ms": activated_at_ms,
        "activation_sha256": activation_hash,
    }


def generation_for_identity(identity: dict[str, Any]) -> str:
    if not isinstance(identity, dict):
        raise ValueError("decision view identity must be a JSON object")
    expected_keys = {
        "vehicle_id",
        "run_id",
        "worker_pid",
        "activation_engine_id",
        "activation_activated_at_ms",
        "activation_sha256",
    }
    if set(identity) != expected_keys:
        raise ValueError("decision view identity has an unexpected key set")
    for field in ("vehicle_id", "run_id", "activation_engine_id"):
        if type(identity.get(field)) is not str or not identity[field]:
            raise ValueError(f"decision view identity {field} must be a non-empty string")
    if not _is_nonnegative_int(identity.get("worker_pid")) or identity["worker_pid"] <= 0:
        raise ValueError("decision view identity worker_pid must be a positive safe int")
    if not _is_nonnegative_int(identity.get("activation_activated_at_ms")):
        raise ValueError(
            "decision view identity activation_activated_at_ms must be a non-negative safe int"
        )
    activation_hash = identity.get("activation_sha256")
    if type(activation_hash) is not str or GENERATION_RE.fullmatch(activation_hash) is None:
        raise ValueError("decision view identity activation_sha256 must be a lower-case SHA-256 hash")
    return _sha256(_canonical(identity))


def parse_generation_query(query: str) -> str:
    """Parse the one required lower-case generation hash query value."""

    if type(query) is not str:
        raise DecisionViewHTTPError(
            400,
            "invalid_generation",
            "decision view requires a query string",
        )
    try:
        parsed = parse_qs(
            query,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=2,
        )
    except (TypeError, ValueError) as exc:
        raise DecisionViewHTTPError(400, "invalid_generation", str(exc)) from exc
    if set(parsed) != {"generation"} or len(parsed.get("generation", [])) != 1:
        raise DecisionViewHTTPError(
            400,
            "invalid_generation",
            "decision view requires exactly one generation query parameter",
        )
    generation = parsed["generation"][0]
    if not isinstance(generation, str) or GENERATION_RE.fullmatch(generation) is None:
        raise DecisionViewHTTPError(
            400,
            "invalid_generation",
            "generation must be a lower-case SHA-256 hash",
        )
    return generation


def parse_image_id(path: str) -> str:
    """Validate a registered image hash without accepting path-like values."""

    if type(path) is not str or not path.startswith(DECISION_IMAGE_PATH):
        raise DecisionViewHTTPError(400, "invalid_image_id", "image id is invalid")
    encoded = path[len(DECISION_IMAGE_PATH) :]
    if not encoded or "/" in encoded:
        raise DecisionViewHTTPError(400, "invalid_image_id", "image id is invalid")
    try:
        image_id = unquote(encoded, errors="strict")
    except (UnicodeDecodeError, ValueError) as exc:
        raise DecisionViewHTTPError(400, "invalid_image_id", str(exc)) from exc
    if image_id != encoded or IMAGE_ID_RE.fullmatch(image_id) is None:
        raise DecisionViewHTTPError(400, "invalid_image_id", "image id is invalid")
    return image_id


def _unavailable_image_descriptor(
    *,
    generation_id: str | None,
    frame_id: str | None,
    frame_index: int | None,
    captured_at_ms: int | None,
    observation_id: str | None,
    reason: str,
    environment_frame: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if type(reason) is not str or not reason:
        raise ValueError("unavailable image descriptor requires a reason")
    if generation_id is not None and (
        type(generation_id) is not str or GENERATION_RE.fullmatch(generation_id) is None
    ):
        raise ValueError("unavailable image descriptor generation_id is invalid")
    if frame_id is not None and (type(frame_id) is not str or not frame_id):
        raise ValueError("unavailable image descriptor frame_id is invalid")
    if frame_index is not None and not _is_nonnegative_int(frame_index):
        raise ValueError("unavailable image descriptor frame_index is invalid")
    if captured_at_ms is not None and not _is_nonnegative_int(captured_at_ms):
        raise ValueError("unavailable image descriptor captured_at_ms is invalid")
    if observation_id is not None and (type(observation_id) is not str or not observation_id):
        raise ValueError("unavailable image descriptor observation_id is invalid")
    descriptor: dict[str, Any] = {
        "status": "unavailable",
        "reason": reason,
        "image_id": None,
        "sha256": None,
        "content_type": None,
        "byte_length": None,
        "width_px": None,
        "height_px": None,
        "generation_id": generation_id,
        "frame_id": frame_id,
        "frame_index": frame_index,
        "captured_at_ms": captured_at_ms,
        "observation_id": observation_id,
        "url": None,
    }
    if environment_frame is not None:
        if not isinstance(environment_frame, dict):
            raise ValueError("unavailable image descriptor environment_frame must be an object")
        descriptor["environment_frame"] = _copy_json(environment_frame)
    return descriptor


def _unavailable_payload(
    *,
    reason: str,
    served_at_ms: int | None = None,
    identity: dict[str, Any] | None = None,
    generation_id: str | None = None,
    max_age_ms: int = DECISION_STREAM_MAX_AGE_MS,
    limits: dict[str, int] | None = None,
) -> dict[str, Any]:
    if type(reason) is not str or not reason:
        raise ValueError("unavailable decision view requires a reason")
    if served_at_ms is not None and not _is_nonnegative_int(served_at_ms):
        raise ValueError("unavailable decision view served_at_ms is invalid")
    if not _is_nonnegative_int(max_age_ms) or max_age_ms <= 0:
        raise ValueError("unavailable decision view max_age_ms must be a positive safe int")
    copied_identity: dict[str, Any] | None = None
    if identity is not None:
        if not isinstance(identity, dict):
            raise ValueError("unavailable decision view identity must be an object")
        copied_identity = _copy_json(identity)
    if generation_id is not None and (
        type(generation_id) is not str or GENERATION_RE.fullmatch(generation_id) is None
    ):
        raise ValueError("unavailable decision view generation_id is invalid")
    copied_limits = default_limits() if limits is None else _copy_json(limits)
    return {
        "schema": DECISION_VIEW_SCHEMA,
        "view_id": DECISION_VIEW_ID,
        "status": "unavailable",
        "reason": reason,
        "served_at_ms": served_at_ms if served_at_ms is not None else timestamp_ms(),
        "max_age_ms": max_age_ms,
        "expires_at_ms": None,
        "generation_id": generation_id,
        "identity": copied_identity,
        "decision": None,
        "decision_sha256": None,
        "current_image": _unavailable_image_descriptor(
            generation_id=generation_id,
            frame_id=None,
            frame_index=None,
            captured_at_ms=None,
            observation_id=None,
            reason=reason,
        ),
        "evidence": [],
        "host_observation": {
            "status": "unavailable",
            "reason": reason,
            "value": None,
        },
        "limits": copied_limits,
    }


def default_limits() -> dict[str, int]:
    return {
        "max_images": MAX_IMAGES,
        "max_image_bytes": MAX_IMAGE_BYTES,
        "max_total_image_bytes": MAX_TOTAL_IMAGE_BYTES,
        "max_pixels_per_image": MAX_PIXELS_PER_IMAGE,
        "max_metadata_bytes": MAX_METADATA_BYTES,
        "max_response_bytes": MAX_RESPONSE_BYTES,
        "image_count": 0,
        "image_bytes": 0,
        "metadata_bytes": 0,
        "retention_refusals": 0,
        "size_refusals": 0,
    }


class DecisionViewPublisher:
    """One worker-generation D2 projection and volatile source-image store."""

    def __init__(
        self,
        *,
        vehicle_id: str,
        automation_dir: Path,
        activation: dict[str, Any],
        run_id: str,
        worker_pid: int,
        decision_runtime_dir: Path | None = None,
        max_age_ms: int = DECISION_STREAM_MAX_AGE_MS,
    ) -> None:
        self.vehicle_id = vehicle_id
        self.automation_dir = Path(automation_dir)
        self.activation_path = (
            Path(decision_runtime_dir) / "active.json"
            if decision_runtime_dir is not None
            else self.automation_dir.parent / "decision" / "active.json"
        )
        self.latest_decision_path = self.automation_dir / "latest_decision.json"
        self.startup_activation = _copy_json(activation)
        self.identity = identity_for_activation(
            vehicle_id=vehicle_id,
            run_id=run_id,
            worker_pid=worker_pid,
            activation=self.startup_activation,
        )
        if self.identity["activation_engine_id"] != "shadow-proposals":
            raise ValueError("D2 decision view requires shadow-proposals")
        if type(max_age_ms) is not int or max_age_ms <= 0:
            raise ValueError("decision view max_age_ms must be a positive int")
        self.max_age_ms = max_age_ms
        self.generation_id = generation_for_identity(self.identity)
        self._lock = threading.RLock()
        self._entries: OrderedDict[str, _ImageRecord] = OrderedDict()
        self._archives: OrderedDict[tuple[str, str], _ObservationArchive] = OrderedDict()
        self._pinned_frame_ids: set[str] = set()
        self._metadata_bytes = 0
        self._retention_refusals = 0
        self._size_refusals = 0
        self._stopped = False

    def describe(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schema": DECISION_VIEW_SCHEMA,
                "view_id": DECISION_VIEW_ID,
                "available": not self._stopped,
                "status": "running" if not self._stopped else "stopped",
                "generation_id": self.generation_id,
                "identity": deepcopy(self.identity),
                "max_age_ms": self.max_age_ms,
                "limits": self._limits_locked(),
            }

    def publish_capture(
        self,
        *,
        frame_bytes: bytes,
        frame_record: dict[str, Any],
        content_type: str,
        width_px: int | None = None,
        height_px: int | None = None,
    ) -> None:
        """Register exact ingress bytes before the producer can remove its file."""

        if self._stopped:
            return
        try:
            frame_id = frame_record.get("frame_id")
            frame_index = frame_record.get("frame_index")
            captured_at_ms = frame_record.get("captured_at_ms")
            if type(frame_id) is not str or not frame_id:
                raise _CaptureRefusal("source_unavailable", "capture frame_id is missing")
            if not _is_nonnegative_int(frame_index):
                raise _CaptureRefusal("source_unavailable", "capture frame_index is invalid")
            if not _is_nonnegative_int(captured_at_ms):
                raise _CaptureRefusal("source_unavailable", "capture timestamp is invalid")
            if type(frame_bytes) is not bytes or not frame_bytes:
                raise _CaptureRefusal("source_unavailable", "capture image is empty")
            if len(frame_bytes) > MAX_IMAGE_BYTES:
                raise _CaptureRefusal("size_limit", "capture image exceeds max_image_bytes")
            normalized_type = str(content_type).lower()
            if normalized_type not in IMAGE_CONTENT_TYPES:
                raise _CaptureRefusal("size_limit", "only PNG/JPEG decision images are supported")
            decoded_width, decoded_height, image_format = _decode_image_dimensions(frame_bytes)
            if image_format == "JPEG":
                normalized_type = "image/jpeg"
            elif image_format == "PNG":
                normalized_type = "image/png"
            else:
                raise _CaptureRefusal("size_limit", "only PNG/JPEG decision images are supported")
            if decoded_width * decoded_height > MAX_PIXELS_PER_IMAGE:
                raise _CaptureRefusal("size_limit", "capture image exceeds max_pixels_per_image")
            if width_px is not None and type(width_px) is int and width_px != decoded_width:
                raise _CaptureRefusal("source_conflict", "capture width disagrees with decoded image")
            if height_px is not None and type(height_px) is int and height_px != decoded_height:
                raise _CaptureRefusal("source_conflict", "capture height disagrees with decoded image")
            sensor_snapshot = frame_record.get("sensor_snapshot")
            if sensor_snapshot is not None and not isinstance(sensor_snapshot, dict):
                raise _CaptureRefusal("source_unavailable", "capture sensor_snapshot is invalid")
            environment_frame = _environment_frame(frame_record)
            metadata = {
                "frame_id": frame_id,
                "frame_index": frame_index,
                "captured_at_ms": captured_at_ms,
                "sensor_snapshot": sensor_snapshot,
                "environment_frame": environment_frame,
            }
            metadata_bytes = len(_canonical(metadata))
            if metadata_bytes > MAX_METADATA_BYTES:
                raise _CaptureRefusal("size_limit", "capture metadata exceeds max_metadata_bytes")
        except _CaptureRefusal as refusal:
            with self._lock:
                self._count_refusal_locked(refusal.reason)
            return
        except (OSError, TypeError, ValueError, UnidentifiedImageError) as exc:
            with self._lock:
                self._count_refusal_locked("size_limit")
            return

        record = _ImageRecord(
            frame_id=frame_id,
            frame_index=frame_index,
            captured_at_ms=captured_at_ms,
            content_type=normalized_type,
            data=bytes(frame_bytes),
            width_px=decoded_width,
            height_px=decoded_height,
            sensor_snapshot=deepcopy(sensor_snapshot) if isinstance(sensor_snapshot, dict) else None,
            environment_frame=deepcopy(environment_frame),
        )
        with self._lock:
            existing = self._entries.get(frame_id)
            if existing is not None:
                if not _same_capture(existing, record):
                    self._entries[frame_id] = _ImageRecord(
                        **{**existing.__dict__, "association_error": "source_conflict"}
                    )
                return
            if not self._admit_entry_locked(record, metadata_bytes):
                return
            self._entries[frame_id] = record
            self._entries.move_to_end(frame_id)
            self._metadata_bytes += metadata_bytes
            archive = next(
                (
                    item
                    for key, item in reversed(self._archives.items())
                    if key[0] == frame_id
                ),
                None,
            )
            if archive is not None:
                self._bind_entry_to_archive_locked(frame_id, archive)

    def publish_decision_frame(self, frame: dict[str, Any]) -> bool:
        """Archive an already-produced accepted cycle and seal its associations."""

        if self._stopped or not isinstance(frame, dict):
            return False
        try:
            accept_decision_stream_frame(
                frame,
                activation=self.startup_activation,
                automation_state={
                    "run_id": self.identity["run_id"],
                    "status": "running",
                    "pid": self.identity["worker_pid"],
                },
                now_ms=frame.get("published_at_ms")
                if type(frame.get("published_at_ms")) is int
                else timestamp_ms(),
                is_pid_alive=lambda _pid: True,
                max_age_ms=self.max_age_ms,
            )
            if frame.get("vehicle_id") != self.vehicle_id:
                return False
            if frame.get("run_id") != self.identity["run_id"]:
                return False
            if frame.get("worker_pid") != self.identity["worker_pid"]:
                return False
            if frame.get("activation_activated_at_ms") != self.identity["activation_activated_at_ms"]:
                return False
        except (DecisionSurfaceError, TypeError, ValueError):
            return False

        try:
            stored_frame = _copy_json(frame)
        except (TypeError, ValueError):
            return False
        cycle = stored_frame.get("cycle")
        if not isinstance(cycle, dict):
            return False
        with self._lock:
            self._update_pins_locked(stored_frame)
            self._archive_cycle_locked(stored_frame, cycle)
        return True

    def latest_payload(
        self,
        *,
        generation: str,
        now_ms: int | None = None,
        pid_alive: Callable[[int], bool] = is_pid_alive,
    ) -> dict[str, Any]:
        if generation != self.generation_id:
            raise DecisionViewHTTPError(
                409,
                "generation_mismatch",
                "requested generation does not belong to this decision view",
                identity=self.identity,
            )
        for attempt in range(2):
            served_at_ms = timestamp_ms() if now_ms is None else int(now_ms)
            frame, frame_bytes = self._accepted_frame(
                generation=generation,
                served_at_ms=served_at_ms,
                pid_alive=pid_alive,
            )
            with self._lock:
                snapshot = self._snapshot_locked()
            payload = self._build_payload(
                frame=frame,
                snapshot=snapshot,
                served_at_ms=served_at_ms,
            )
            if self._latest_bytes() != frame_bytes:
                if attempt == 0:
                    continue
                raise DecisionViewHTTPError(
                    503,
                    "decision_changed_during_read",
                    "latest decision changed while the view was assembled",
                    identity=self.identity,
                )
            self._recheck_generation(generation=generation, pid_alive=pid_alive)
            try:
                response_bytes = _canonical(payload)
            except (TypeError, ValueError) as exc:
                raise DecisionViewHTTPError(
                    503,
                    "view_payload_invalid",
                    f"decision view is not strictly JSON serializable: {exc}",
                    identity=self.identity,
                ) from exc
            if len(response_bytes) > MAX_RESPONSE_BYTES:
                raise DecisionViewHTTPError(
                    503,
                    "view_payload_too_large",
                    "decision view response exceeds max_response_bytes",
                    identity=self.identity,
                )
            return payload
        raise AssertionError("decision view retry loop did not return")

    def image_response(
        self,
        *,
        image_id: str,
        generation: str,
        pid_alive: Callable[[int], bool] = is_pid_alive,
    ) -> tuple[bytes, str, str]:
        if generation != self.generation_id:
            raise DecisionViewHTTPError(
                409,
                "generation_mismatch",
                "requested generation does not belong to this decision view",
                identity=self.identity,
            )
        self._accepted_frame(
            generation=generation,
            served_at_ms=timestamp_ms(),
            pid_alive=pid_alive,
        )
        with self._lock:
            record = next(
                (
                    item
                    for item in self._entries.values()
                    if item.image_id == image_id and item.association_error is None
                ),
                None,
            )
            if record is None:
                raise DecisionViewHTTPError(404, "image_not_found", "registered image was not found")
            if record.sha256 is None or _sha256(record.data) != record.sha256:
                raise DecisionViewHTTPError(
                    503,
                    "image_unavailable",
                    "registered image bytes failed their immutable hash check",
                    identity=self.identity,
                )
            return record.data, record.content_type, record.sha256

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            self._entries.clear()
            self._archives.clear()
            self._pinned_frame_ids.clear()
            self._metadata_bytes = 0

    def _accepted_frame(
        self,
        *,
        generation: str,
        served_at_ms: int,
        pid_alive: Callable[[int], bool],
    ) -> tuple[dict[str, Any], bytes]:
        if generation != self.generation_id:
            raise DecisionViewHTTPError(
                409,
                "generation_mismatch",
                "requested generation does not belong to this decision view",
                identity=self.identity,
            )
        if self._stopped:
            raise DecisionViewHTTPError(
                503,
                "producer_unavailable",
                "decision view publisher is stopped",
                identity=self.identity,
            )
        try:
            activation = _read_surface_activation(
                self.activation_path,
                vehicle_id=self.vehicle_id,
            )
        except FileNotFoundError as exc:
            raise DecisionViewHTTPError(
                503, "producer_unavailable", "decision activation is unavailable"
            ) from exc
        except (OSError, json.JSONDecodeError, TypeError, ValueError, DecisionSurfaceError) as exc:
            raise DecisionViewHTTPError(
                503, "activation_invalid", "decision activation is invalid"
            ) from exc

        if activation["decision"]["engine_id"] != "shadow-proposals":
            raise DecisionViewHTTPError(
                503,
                "wrong_engine",
                "decision view requires engine_id='shadow-proposals'",
                identity=self.identity,
            )

        try:
            current_identity = identity_for_activation(
                vehicle_id=self.vehicle_id,
                run_id=self.identity["run_id"],
                worker_pid=self.identity["worker_pid"],
                activation=activation,
            )
        except (TypeError, ValueError) as exc:
            raise DecisionViewHTTPError(503, "activation_invalid", str(exc)) from exc
        if current_identity != self.identity:
            raise DecisionViewHTTPError(
                409,
                "generation_mismatch",
                "current activation does not match the view startup generation",
                identity=self.identity,
            )

        state = self._read_state()
        if state is None:
            raise DecisionViewHTTPError(
                503,
                "producer_unavailable",
                "automation state is unavailable",
                identity=self.identity,
            )
        if state.get("vehicle_id") not in (None, self.vehicle_id):
            raise DecisionViewHTTPError(503, "vehicle_mismatch", "automation state belongs to another vehicle")
        if state.get("run_id") != self.identity["run_id"] or state.get("pid") != self.identity["worker_pid"]:
            raise DecisionViewHTTPError(
                503,
                "generation_mismatch",
                "automation state does not match the view startup generation",
                identity=self.identity,
            )
        frame, frame_bytes = self._read_frame()
        try:
            accept_decision_stream_frame(
                frame,
                activation=activation,
                automation_state=state,
                now_ms=served_at_ms,
                is_pid_alive=pid_alive,
                max_age_ms=self.max_age_ms,
            )
        except DecisionSurfaceError as exc:
            status, reason = _route_error_for_decision(exc)
            raise DecisionViewHTTPError(
                status,
                reason,
                exc.message_text,
                identity=self.identity,
            ) from exc
        if frame.get("vehicle_id") is not None and frame.get("vehicle_id") != self.vehicle_id:
            raise DecisionViewHTTPError(503, "vehicle_mismatch", "latest decision belongs to another vehicle")
        if (
            frame.get("run_id") is not None
            and frame.get("run_id") != self.identity["run_id"]
        ) or (
            frame.get("worker_pid") is not None
            and frame.get("worker_pid") != self.identity["worker_pid"]
        ):
            raise DecisionViewHTTPError(
                503,
                "generation_mismatch",
                "latest decision does not match the view startup generation",
                identity=self.identity,
            )
        return frame, frame_bytes

    def _recheck_generation(
        self,
        *,
        generation: str,
        pid_alive: Callable[[int], bool],
    ) -> None:
        self._accepted_frame(
            generation=generation,
            served_at_ms=timestamp_ms(),
            pid_alive=pid_alive,
        )

    def _read_frame(self) -> tuple[dict[str, Any], bytes]:
        try:
            with self.latest_decision_path.open("rb") as stream:
                raw = stream.read(MAX_DECISION_FILE_BYTES + 1)
        except FileNotFoundError as exc:
            raise DecisionViewHTTPError(503, "decision_missing", "latest decision publication is missing") from exc
        except OSError as exc:
            raise DecisionViewHTTPError(503, "decision_unreachable", str(exc)) from exc
        if len(raw) > MAX_DECISION_FILE_BYTES:
            raise DecisionViewHTTPError(503, "view_payload_too_large", "latest decision file exceeds the D2 read bound")
        try:
            frame = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DecisionViewHTTPError(422, "decision_invalid", f"latest decision publication is malformed: {exc}") from exc
        if not isinstance(frame, dict):
            raise DecisionViewHTTPError(422, "decision_invalid", "latest decision publication is not an object")
        return frame, raw

    def _latest_bytes(self) -> bytes | None:
        try:
            with self.latest_decision_path.open("rb") as stream:
                raw = stream.read(MAX_DECISION_FILE_BYTES + 1)
        except OSError:
            return None
        if len(raw) > MAX_DECISION_FILE_BYTES:
            return None
        return raw

    def _read_state(self) -> dict[str, Any] | None:
        state_path = self.automation_dir / "state.json"
        try:
            with state_path.open("rb") as stream:
                raw = stream.read(MAX_RECORD_BYTES + 1)
        except OSError:
            return None
        if len(raw) > MAX_RECORD_BYTES:
            return None
        try:
            state = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(state, dict) or state.get("status") != "running":
            return None
        return state

    def _archive_cycle_locked(self, frame: dict[str, Any], cycle: dict[str, Any]) -> None:
        source = cycle.get("source")
        if not isinstance(source, dict):
            return
        if source.get("frame_id") != frame.get("frame_id"):
            return
        observation_env = source.get("observation")
        if not isinstance(observation_env, dict) or observation_env.get("status") != "ready":
            return
        observation = observation_env.get("value")
        if not isinstance(observation, dict):
            return
        observation_id = observation.get("observation_id")
        if type(observation_id) is not str or not observation_id:
            return
        frame_id = str(frame.get("frame_id"))
        frame_index = frame.get("frame_index") if _is_nonnegative_int(frame.get("frame_index")) else None
        entry = self._entries.get(frame_id)
        captured_at_ms = entry.captured_at_ms if entry is not None else None
        environment_frame = entry.environment_frame if entry is not None else None
        association_error = None
        if entry is not None:
            association_error = self._bind_observation_to_entry_locked(
                entry=entry,
                observation=observation,
                observation_id=observation_id,
            )
        archive = _ObservationArchive(
            frame_id=frame_id,
            frame_index=frame_index,
            captured_at_ms=captured_at_ms,
            observation_id=observation_id,
            observation=deepcopy(observation),
            association_error=association_error,
            environment_frame=deepcopy(environment_frame),
        )
        previous = self._archives.pop((frame_id, observation_id), None)
        if previous is not None:
            self._metadata_bytes -= _archive_metadata_bytes(previous)
        try:
            archive_metadata_bytes = _archive_metadata_bytes(archive)
        except (TypeError, ValueError):
            archive_metadata_bytes = MAX_METADATA_BYTES + 1
        if archive_metadata_bytes > MAX_METADATA_BYTES:
            if previous is not None:
                self._archives[(frame_id, observation_id)] = previous
                self._metadata_bytes += _archive_metadata_bytes(previous)
            self._count_refusal_locked("size_limit")
            return
        if not self._make_metadata_room_locked(archive_metadata_bytes):
            if previous is not None:
                self._archives[(frame_id, observation_id)] = previous
                self._metadata_bytes += _archive_metadata_bytes(previous)
            self._count_refusal_locked("size_limit")
            return
        self._archives[(frame_id, observation_id)] = archive
        self._metadata_bytes += archive_metadata_bytes

    def _bind_observation_to_entry_locked(
        self,
        *,
        entry: _ImageRecord,
        observation: dict[str, Any],
        observation_id: str,
    ) -> str | None:
        if entry.association_error is not None:
            return entry.association_error
        observed_snapshot = observation.get("sensor_snapshot")
        if entry.sensor_snapshot is None or not isinstance(observed_snapshot, dict):
            error = "source_association_unavailable"
        else:
            try:
                error = None if _canonical(entry.sensor_snapshot) == _canonical(observed_snapshot) else "source_association_mismatch"
            except (TypeError, ValueError):
                error = "source_association_mismatch"
        if error is not None:
            self._replace_entry_error_locked(entry.frame_id, error)
            return error
        if entry.observation_id is not None and entry.observation_id != observation_id:
            self._replace_entry_error_locked(entry.frame_id, "source_conflict")
            return "source_conflict"
        if entry.observation_id == observation_id:
            return None
        updated = _ImageRecord(
            **{
                **entry.__dict__,
                "observation_id": observation_id,
                "image_id": None,
                "sha256": None,
            }
        )
        descriptor_base = _image_descriptor_base(
            generation_id=self.generation_id,
            frame_id=updated.frame_id,
            frame_index=updated.frame_index,
            captured_at_ms=updated.captured_at_ms,
            observation_id=observation_id,
            sha256=_sha256(updated.data),
            content_type=updated.content_type,
            byte_length=len(updated.data),
            width_px=updated.width_px,
            height_px=updated.height_px,
            environment_frame=updated.environment_frame,
        )
        updated = _ImageRecord(
            **{
                **updated.__dict__,
                "image_id": _sha256(_canonical(descriptor_base)),
                "sha256": descriptor_base["sha256"],
            }
        )
        self._entries[entry.frame_id] = updated
        self._entries.move_to_end(entry.frame_id)
        return None

    def _bind_entry_to_archive_locked(
        self,
        frame_id: str,
        archive: _ObservationArchive,
    ) -> None:
        entry = self._entries.get(frame_id)
        if entry is None:
            return
        self._bind_observation_to_entry_locked(
            entry=entry,
            observation=archive.observation,
            observation_id=archive.observation_id,
        )

    def _replace_entry_error_locked(self, frame_id: str, error: str) -> None:
        entry = self._entries.get(frame_id)
        if entry is None:
            return
        self._entries[frame_id] = _ImageRecord(
            **{
                **entry.__dict__,
                "association_error": error,
                "image_id": None,
                "sha256": None,
            }
        )

    def _update_pins_locked(self, frame: dict[str, Any]) -> None:
        pins: set[str] = set()
        frame_id = frame.get("frame_id")
        if isinstance(frame_id, str):
            pins.add(frame_id)
        cycle = frame.get("cycle")
        source = cycle.get("source") if isinstance(cycle, dict) else None
        memory = source.get("memory") if isinstance(source, dict) else None
        value = memory.get("value") if isinstance(memory, dict) else None
        records = value.get("records") if isinstance(value, dict) else None
        if isinstance(records, list):
            for record in records:
                if not isinstance(record, dict):
                    continue
                provenance = record.get("provenance")
                source_frame_id = provenance.get("frame_id") if isinstance(provenance, dict) else None
                if isinstance(source_frame_id, str):
                    pins.add(source_frame_id)
        self._pinned_frame_ids = pins

    def _admit_entry_locked(self, record: _ImageRecord, metadata_bytes: int) -> bool:
        if (
            type(metadata_bytes) is not int
            or metadata_bytes < 0
            or metadata_bytes > MAX_METADATA_BYTES
            or type(record.data) is not bytes
            or not record.data
            or len(record.data) > MAX_IMAGE_BYTES
            or type(record.width_px) is not int
            or type(record.height_px) is not int
            or record.width_px <= 0
            or record.height_px <= 0
            or record.width_px * record.height_px > MAX_PIXELS_PER_IMAGE
        ):
            self._count_refusal_locked("size_limit")
            return False

        required_bytes = len(record.data)
        count_pressure = len(self._entries) >= MAX_IMAGES
        metadata_pressure = self._metadata_bytes + metadata_bytes > MAX_METADATA_BYTES
        if metadata_pressure:
            has_unpinned_entry = any(
                frame_id not in self._pinned_frame_ids
                and entry.frame_id not in self._pinned_frame_ids
                for frame_id, entry in self._entries.items()
            )
            if not self._make_metadata_room_locked(metadata_bytes):
                self._count_refusal_locked(
                    "retention_limit"
                    if count_pressure and not has_unpinned_entry
                    else "size_limit"
                )
                return False

        image_pressure = (
            len(self._entries) >= MAX_IMAGES
            or self._image_bytes_locked() + required_bytes > MAX_TOTAL_IMAGE_BYTES
        )
        if image_pressure:
            if not self._evict_for_locked(required_bytes=required_bytes, required_metadata=0):
                has_unpinned_entry = any(
                    frame_id not in self._pinned_frame_ids
                    and entry.frame_id not in self._pinned_frame_ids
                    for frame_id, entry in self._entries.items()
                )
                self._count_refusal_locked(
                    "retention_limit"
                    if len(self._entries) >= MAX_IMAGES and not has_unpinned_entry
                    else "size_limit"
                )
                return False
        self._entries[record.frame_id] = record
        self._entries.move_to_end(record.frame_id)
        return True

    def _evict_for_locked(self, *, required_bytes: int, required_metadata: int) -> bool:
        if (
            type(required_bytes) is not int
            or required_bytes < 0
            or required_bytes > MAX_IMAGE_BYTES
            or type(required_metadata) is not int
            or required_metadata < 0
            or required_metadata > MAX_METADATA_BYTES
        ):
            return False

        image_bytes = self._image_bytes_locked()
        if (
            len(self._entries) < MAX_IMAGES
            and image_bytes + required_bytes <= MAX_TOTAL_IMAGE_BYTES
            and self._metadata_bytes + required_metadata <= MAX_METADATA_BYTES
        ):
            return True

        for frame_id, entry in list(self._entries.items()):
            if frame_id in self._pinned_frame_ids or entry.frame_id in self._pinned_frame_ids:
                continue
            self._entries.pop(frame_id, None)
            image_bytes -= len(entry.data)
            self._metadata_bytes = max(
                0,
                self._metadata_bytes - _entry_metadata_bytes(entry),
            )
            if (
                len(self._entries) < MAX_IMAGES
                and image_bytes + required_bytes <= MAX_TOTAL_IMAGE_BYTES
                and self._metadata_bytes + required_metadata <= MAX_METADATA_BYTES
            ):
                return True
        return (
            len(self._entries) < MAX_IMAGES
            and image_bytes + required_bytes <= MAX_TOTAL_IMAGE_BYTES
            and self._metadata_bytes + required_metadata <= MAX_METADATA_BYTES
        )

    def _make_metadata_room_locked(self, required_metadata: int) -> bool:
        """Evict only older unpinned records before refusing new metadata."""

        if (
            type(required_metadata) is not int
            or required_metadata < 0
            or required_metadata > MAX_METADATA_BYTES
        ):
            return False

        if self._metadata_bytes + required_metadata <= MAX_METADATA_BYTES:
            return True
        for key, archive in list(self._archives.items()):
            if self._metadata_bytes + required_metadata <= MAX_METADATA_BYTES:
                return True
            if key[0] in self._pinned_frame_ids or archive.frame_id in self._pinned_frame_ids:
                continue
            self._archives.pop(key, None)
            self._metadata_bytes = max(
                0,
                self._metadata_bytes - _archive_metadata_bytes(archive),
            )
        if self._metadata_bytes + required_metadata <= MAX_METADATA_BYTES:
            return True
        return self._evict_for_locked(
            required_bytes=0,
            required_metadata=required_metadata,
        )

    def _snapshot_locked(self) -> _StoreSnapshot:
        return _StoreSnapshot(
            entries=dict(self._entries),
            archives=dict(self._archives),
            image_count=len(self._entries),
            image_bytes=self._image_bytes_locked(),
            metadata_bytes=self._metadata_bytes,
            retention_refusals=self._retention_refusals,
            size_refusals=self._size_refusals,
        )

    def _image_bytes_locked(self) -> int:
        return sum(len(item.data) for item in self._entries.values())

    def _limits_locked(self) -> dict[str, int]:
        image_bytes = self._image_bytes_locked()
        return {
            "max_images": MAX_IMAGES,
            "max_image_bytes": MAX_IMAGE_BYTES,
            "max_total_image_bytes": MAX_TOTAL_IMAGE_BYTES,
            "max_pixels_per_image": MAX_PIXELS_PER_IMAGE,
            "max_metadata_bytes": MAX_METADATA_BYTES,
            "max_response_bytes": MAX_RESPONSE_BYTES,
            "image_count": len(self._entries),
            "image_bytes": image_bytes,
            "metadata_bytes": max(0, self._metadata_bytes),
            "retention_refusals": max(0, self._retention_refusals),
            "size_refusals": max(0, self._size_refusals),
        }

    def _count_refusal_locked(self, reason: str) -> None:
        if reason in {"retention_limit", "retention_refusal"}:
            self._retention_refusals += 1
        elif reason in {"size_limit", "size_refusal"}:
            self._size_refusals += 1

    def _build_payload(
        self,
        *,
        frame: dict[str, Any],
        snapshot: _StoreSnapshot,
        served_at_ms: int,
    ) -> dict[str, Any]:
        try:
            decision_sha = _sha256(_canonical(frame))
        except (TypeError, ValueError) as exc:
            raise DecisionViewHTTPError(422, "decision_invalid", str(exc), identity=self.identity) from exc
        current_image = _resolve_current_image(
            frame=frame,
            generation_id=self.generation_id,
            entries=snapshot.entries,
        )
        evidence = _resolve_evidence(
            frame=frame,
            generation_id=self.generation_id,
            entries=snapshot.entries,
            archives=snapshot.archives,
        )
        host_observation = _resolve_host_observation(
            frame=frame,
            identity=self.identity,
        )
        reason = _first_view_reason(current_image, evidence, host_observation)
        status = "current" if reason is None else "partial"
        published_at_ms = frame.get("published_at_ms")
        expires_at_ms = (
            published_at_ms + self.max_age_ms
            if type(published_at_ms) is int
            else None
        )
        return {
            "schema": DECISION_VIEW_SCHEMA,
            "view_id": DECISION_VIEW_ID,
            "status": status,
            "reason": reason,
            "served_at_ms": served_at_ms,
            "max_age_ms": self.max_age_ms,
            "expires_at_ms": expires_at_ms,
            "generation_id": self.generation_id,
            "identity": deepcopy(self.identity),
            "decision": deepcopy(frame),
            "decision_sha256": decision_sha,
            "current_image": current_image,
            "evidence": evidence,
            "host_observation": host_observation,
            "limits": {
                "max_images": MAX_IMAGES,
                "max_image_bytes": MAX_IMAGE_BYTES,
                "max_total_image_bytes": MAX_TOTAL_IMAGE_BYTES,
                "max_pixels_per_image": MAX_PIXELS_PER_IMAGE,
                "max_metadata_bytes": MAX_METADATA_BYTES,
                "max_response_bytes": MAX_RESPONSE_BYTES,
                "image_count": snapshot.image_count,
                "image_bytes": snapshot.image_bytes,
                "metadata_bytes": snapshot.metadata_bytes,
                "retention_refusals": snapshot.retention_refusals,
                "size_refusals": snapshot.size_refusals,
            },
        }


class _CaptureRefusal(Exception):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def _route_error_for_decision(exc: DecisionSurfaceError) -> tuple[int, str]:
    if exc.error == "latest_frame_invalid":
        return 422, "decision_invalid"
    if exc.error == "generation_mismatch":
        return 409, "generation_mismatch"
    if exc.error == "latest_frame_missing":
        return 503, "decision_missing"
    if exc.error == "wrong_engine":
        return 503, "wrong_engine"
    if exc.error == "activation_missing":
        return 503, "producer_unavailable"
    if exc.error == "activation_invalid":
        return 503, "activation_invalid"
    if exc.error == "latest_frame_stale":
        return 503, "decision_stale"
    return 503, "producer_unavailable"


def _decode_image_dimensions(data: bytes) -> tuple[int, int, str]:
    with Image.open(io.BytesIO(data)) as image:
        image.load()
        width, height = image.size
        return int(width), int(height), str(image.format or "")


def _environment_frame(frame_record: dict[str, Any]) -> dict[str, Any] | None:
    epoch = frame_record.get("simulation_epoch")
    index = frame_record.get("simulator_frame_index")
    if epoch is None and index is None:
        return None
    if type(epoch) is not str or not epoch or not _is_nonnegative_int(index):
        return None
    return {
        "simulation_epoch": epoch,
        "simulator_frame_index": index,
    }


def _same_capture(left: _ImageRecord, right: _ImageRecord) -> bool:
    if left.data != right.data:
        return False
    if (
        left.frame_index != right.frame_index
        or left.captured_at_ms != right.captured_at_ms
        or left.content_type != right.content_type
        or left.environment_frame != right.environment_frame
    ):
        return False
    try:
        return _canonical(left.sensor_snapshot) == _canonical(right.sensor_snapshot)
    except (TypeError, ValueError):
        return False


def _entry_metadata_bytes(entry: _ImageRecord) -> int:
    try:
        return len(
            _canonical(
                {
                    "frame_id": entry.frame_id,
                    "frame_index": entry.frame_index,
                    "captured_at_ms": entry.captured_at_ms,
                    "sensor_snapshot": entry.sensor_snapshot,
                    "environment_frame": entry.environment_frame,
                }
            )
        )
    except (TypeError, ValueError):
        return 0


def _archive_metadata_bytes(archive: _ObservationArchive) -> int:
    try:
        return len(
            _canonical(
                {
                    "frame_id": archive.frame_id,
                    "frame_index": archive.frame_index,
                    "captured_at_ms": archive.captured_at_ms,
                    "observation_id": archive.observation_id,
                    "observation": archive.observation,
                    "association_error": archive.association_error,
                    "environment_frame": archive.environment_frame,
                }
            )
        )
    except (TypeError, ValueError):
        return 0


def _image_descriptor_base(
    *,
    generation_id: str,
    frame_id: str,
    frame_index: int,
    captured_at_ms: int,
    observation_id: str,
    sha256: str,
    content_type: str,
    byte_length: int,
    width_px: int,
    height_px: int,
    environment_frame: dict[str, Any] | None,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "sha256": sha256,
        "content_type": content_type,
        "byte_length": byte_length,
        "width_px": width_px,
        "height_px": height_px,
        "generation_id": generation_id,
        "frame_id": frame_id,
        "frame_index": frame_index,
        "captured_at_ms": captured_at_ms,
        "observation_id": observation_id,
    }
    if environment_frame is not None:
        base["environment_frame"] = deepcopy(environment_frame)
    return base


def _available_descriptor(
    *,
    entry: _ImageRecord,
    generation_id: str,
) -> dict[str, Any]:
    if entry.image_id is None or entry.sha256 is None or entry.observation_id is None:
        return _unavailable_image_descriptor(
            generation_id=generation_id,
            frame_id=entry.frame_id,
            frame_index=entry.frame_index,
            captured_at_ms=entry.captured_at_ms,
            observation_id=entry.observation_id,
            reason=entry.association_error or "source_unavailable",
            environment_frame=entry.environment_frame,
        )
    return {
        "status": "available",
        "reason": None,
        "image_id": entry.image_id,
        "sha256": entry.sha256,
        "content_type": entry.content_type,
        "byte_length": len(entry.data),
        "width_px": entry.width_px,
        "height_px": entry.height_px,
        "generation_id": generation_id,
        "frame_id": entry.frame_id,
        "frame_index": entry.frame_index,
        "captured_at_ms": entry.captured_at_ms,
        "observation_id": entry.observation_id,
        "url": (
            f"{DECISION_IMAGE_PATH}{entry.image_id}?"
            f"{urlencode({'generation': generation_id})}"
        ),
        **(
            {"environment_frame": deepcopy(entry.environment_frame)}
            if entry.environment_frame is not None
            else {}
        ),
    }


def _resolve_current_image(
    *,
    frame: dict[str, Any],
    generation_id: str,
    entries: dict[str, _ImageRecord],
) -> dict[str, Any]:
    cycle = frame.get("cycle") if isinstance(frame.get("cycle"), dict) else {}
    source = cycle.get("source") if isinstance(cycle, dict) else None
    frame_id = frame.get("frame_id") if isinstance(frame.get("frame_id"), str) else None
    frame_index = frame.get("frame_index") if _is_nonnegative_int(frame.get("frame_index")) else None
    if not isinstance(source, dict):
        return _unavailable_image_descriptor(
            generation_id=generation_id,
            frame_id=frame_id,
            frame_index=frame_index,
            captured_at_ms=None,
            observation_id=None,
            reason="source_unavailable",
        )
    observation_env = source.get("observation")
    observation = observation_env.get("value") if isinstance(observation_env, dict) and observation_env.get("status") == "ready" else None
    observation_id = observation.get("observation_id") if isinstance(observation, dict) else None
    entry = entries.get(frame_id) if frame_id is not None else None
    if entry is None:
        return _unavailable_image_descriptor(
            generation_id=generation_id,
            frame_id=frame_id,
            frame_index=frame_index,
            captured_at_ms=None,
            observation_id=observation_id if isinstance(observation_id, str) else None,
            reason="source_unavailable",
        )
    if entry.association_error is not None:
        reason = entry.association_error
    elif not isinstance(observation_id, str) or entry.observation_id != observation_id:
        reason = "source_association_mismatch"
    else:
        return _available_descriptor(entry=entry, generation_id=generation_id)
    return _unavailable_image_descriptor(
        generation_id=generation_id,
        frame_id=entry.frame_id,
        frame_index=entry.frame_index,
        captured_at_ms=entry.captured_at_ms,
        observation_id=observation_id if isinstance(observation_id, str) else entry.observation_id,
        reason=reason,
        environment_frame=entry.environment_frame,
    )


def _resolve_evidence(
    *,
    frame: dict[str, Any],
    generation_id: str,
    entries: dict[str, _ImageRecord],
    archives: dict[tuple[str, str], _ObservationArchive],
) -> list[dict[str, Any]]:
    cycle = frame.get("cycle") if isinstance(frame.get("cycle"), dict) else {}
    plan = cycle.get("plan") if isinstance(cycle, dict) else None
    if not isinstance(plan, dict):
        return []
    selected_id = plan.get("selected_proposal_id")
    result: list[dict[str, Any]] = []
    candidates = plan.get("candidates") if isinstance(plan.get("candidates"), list) else []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        proposal_id = candidate.get("proposal_id")
        refs = candidate.get("source_refs") if isinstance(candidate.get("source_refs"), list) else []
        for source_ref in refs:
            if not isinstance(source_ref, dict):
                continue
            result.append(
                _resolve_one_ref(
                    source_ref=source_ref,
                    proposal_id=proposal_id if isinstance(proposal_id, str) else None,
                    selected=proposal_id == selected_id,
                    current_frame_id=frame.get("frame_id"),
                    generation_id=generation_id,
                    cycle=cycle,
                    entries=entries,
                    archives=archives,
                )
            )
    return result


def _resolve_one_ref(
    *,
    source_ref: dict[str, Any],
    proposal_id: str | None,
    selected: bool,
    current_frame_id: Any,
    generation_id: str,
    cycle: dict[str, Any],
    entries: dict[str, _ImageRecord],
    archives: dict[tuple[str, str], _ObservationArchive],
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "proposal_id": proposal_id,
        "selected": bool(selected),
        "source_ref": deepcopy(source_ref),
        "status": "unavailable",
        "reason": "unsupported_ref",
        "association": "unavailable",
        "record_id": None,
        "provenance": None,
        "source_image": _unavailable_image_descriptor(
            generation_id=generation_id,
            frame_id=None,
            frame_index=None,
            captured_at_ms=None,
            observation_id=None,
            reason="unsupported_ref",
        ),
        "geometry": _unavailable_geometry(reason="unsupported_ref", coordinate_frame=None),
    }
    if source_ref.get("kind") != "memory_record":
        return base
    source = cycle.get("source") if isinstance(cycle.get("source"), dict) else None
    memory = source.get("memory") if isinstance(source, dict) else None
    value = memory.get("value") if isinstance(memory, dict) and memory.get("status") == "ready" else None
    records = value.get("records") if isinstance(value, dict) else None
    if not isinstance(records, list):
        base["reason"] = "memory_unavailable"
        base["source_image"]["reason"] = "memory_unavailable"
        base["geometry"]["reason"] = "memory_unavailable"
        return base
    matches = [record for record in records if isinstance(record, dict) and record.get("record_id") == source_ref.get("id")]
    if len(matches) != 1:
        base["reason"] = "source_ambiguous" if len(matches) > 1 else "source_unavailable"
        base["source_image"]["reason"] = base["reason"]
        base["geometry"]["reason"] = base["reason"]
        return base
    record = matches[0]
    provenance = record.get("provenance") if isinstance(record.get("provenance"), dict) else None
    base["record_id"] = record.get("record_id")
    base["provenance"] = deepcopy(provenance)
    ref_to_provenance = {
        "frame_id": "frame_id",
        "observation_id": "observation_id",
        "plugin_id": "source_plugin_id",
    }
    if provenance is None or any(
        source_ref.get(ref_field) != provenance.get(provenance_field)
        for ref_field, provenance_field in ref_to_provenance.items()
    ):
        base["reason"] = "source_ref_mismatch"
        base["source_image"]["reason"] = "source_ref_mismatch"
        base["geometry"]["reason"] = "source_ref_mismatch"
        return base
    source_frame_id = provenance.get("frame_id")
    observation_id = provenance.get("observation_id")
    archive = archives.get((source_frame_id, observation_id)) if isinstance(source_frame_id, str) and isinstance(observation_id, str) else None
    if archive is None:
        base["reason"] = "source_unavailable"
        base["source_image"] = _unavailable_image_descriptor(
            generation_id=generation_id,
            frame_id=source_frame_id if isinstance(source_frame_id, str) else None,
            frame_index=None,
            captured_at_ms=None,
            observation_id=observation_id if isinstance(observation_id, str) else None,
            reason="source_unavailable",
        )
        base["geometry"]["reason"] = "source_unavailable"
        return base
    association = "current" if source_frame_id == current_frame_id else "retained"
    base["association"] = association
    entry = entries.get(source_frame_id) if isinstance(source_frame_id, str) else None
    if entry is None:
        base["source_image"] = _unavailable_image_descriptor(
            generation_id=generation_id,
            frame_id=archive.frame_id,
            frame_index=archive.frame_index,
            captured_at_ms=archive.captured_at_ms,
            observation_id=archive.observation_id,
            reason="retention_limit",
            environment_frame=archive.environment_frame,
        )
        image_reason = "retention_limit"
    elif entry.association_error is not None:
        base["source_image"] = _unavailable_image_descriptor(
            generation_id=generation_id,
            frame_id=entry.frame_id,
            frame_index=entry.frame_index,
            captured_at_ms=entry.captured_at_ms,
            observation_id=archive.observation_id,
            reason=entry.association_error,
            environment_frame=entry.environment_frame,
        )
        image_reason = entry.association_error
    elif entry.observation_id != archive.observation_id:
        base["source_image"] = _unavailable_image_descriptor(
            generation_id=generation_id,
            frame_id=entry.frame_id,
            frame_index=entry.frame_index,
            captured_at_ms=entry.captured_at_ms,
            observation_id=archive.observation_id,
            reason="source_association_mismatch",
            environment_frame=entry.environment_frame,
        )
        image_reason = "source_association_mismatch"
    else:
        base["source_image"] = _available_descriptor(entry=entry, generation_id=generation_id)
        image_reason = None

    observation = archive.observation
    observed_at_ms = provenance.get("observed_at_ms")
    if archive.association_error is not None:
        geometry = _unavailable_geometry(reason=archive.association_error, coordinate_frame=provenance.get("coordinate_frame"))
        geometry_reason = archive.association_error
    elif observation.get("observation_id") != observation_id or observation.get("created_at_ms") != observed_at_ms:
        geometry = _unavailable_geometry(reason="source_observation_mismatch", coordinate_frame=provenance.get("coordinate_frame"))
        geometry_reason = "source_observation_mismatch"
    else:
        matching_things = []
        for thing in observation.get("things") if isinstance(observation.get("things"), list) else []:
            if not isinstance(thing, dict) or thing.get("thing_id") != provenance.get("evidence_id"):
                continue
            thing_plugin = thing.get("source_plugin_id")
            if thing_plugin is None:
                thing_plugin = observation.get("perception_plugin_id")
            if thing_plugin == provenance.get("source_plugin_id"):
                matching_things.append(thing)
        if len(matching_things) != 1:
            geometry = _unavailable_geometry(reason="source_observation_ambiguous", coordinate_frame=provenance.get("coordinate_frame"))
            geometry_reason = "source_observation_ambiguous"
        else:
            thing_location = matching_things[0].get("location")
            record_location = record.get("location")
            if record_location != thing_location:
                geometry = _unavailable_geometry(reason="geometry_conflict", coordinate_frame=provenance.get("coordinate_frame"))
                geometry_reason = "geometry_conflict"
            else:
                geometry = _resolve_geometry(record_location, provenance.get("coordinate_frame"))
                geometry_reason = geometry.get("reason")
    base["geometry"] = geometry
    if image_reason is not None:
        base["reason"] = image_reason
    elif geometry_reason is not None:
        base["reason"] = geometry_reason
    else:
        base["status"] = "available"
        base["reason"] = None
    return base


def _unavailable_geometry(*, reason: str, coordinate_frame: Any) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "reason": reason,
        "coordinate_frame": coordinate_frame if isinstance(coordinate_frame, str) and coordinate_frame else None,
        "bbox_xyxy_norm": None,
        "polygon_xy_norm": None,
        "transform": None,
    }


def _resolve_geometry(location: Any, provenance_frame: Any) -> dict[str, Any]:
    if not isinstance(location, dict):
        return _unavailable_geometry(reason="geometry_missing", coordinate_frame=provenance_frame)
    location_frame = location.get("frame")
    coordinate_frame = provenance_frame if isinstance(provenance_frame, str) else location_frame
    if location_frame != "image" or provenance_frame != "image":
        return _unavailable_geometry(reason="geometry_unsupported", coordinate_frame=coordinate_frame)
    bbox = location.get("bbox_xyxy_norm")
    polygon = location.get("polygon_xy_norm")
    try:
        validated_bbox = _validate_bbox(bbox)
        validated_polygon = _validate_polygon(polygon)
    except ValueError as exc:
        return _unavailable_geometry(reason=str(exc), coordinate_frame="image")
    if validated_bbox is None and validated_polygon is None:
        return _unavailable_geometry(reason="geometry_missing", coordinate_frame="image")
    return {
        "status": "available",
        "reason": None,
        "coordinate_frame": "image",
        "bbox_xyxy_norm": validated_bbox,
        "polygon_xy_norm": validated_polygon,
        "transform": "normalized_source_image_to_display",
    }


def _validate_bbox(value: Any) -> list[float] | None:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != 4 or not all(_is_finite_number(item) for item in value):
        raise ValueError("geometry_invalid")
    values = [float(item) for item in value]
    if not (all(0.0 <= item <= 1.0 for item in values) and values[0] < values[2] and values[1] < values[3]):
        raise ValueError("geometry_invalid")
    return values


def _validate_polygon(value: Any) -> list[list[float]] | None:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) < 3:
        raise ValueError("geometry_invalid")
    if len(value) > MAX_POLYGON_VERTICES:
        raise ValueError("geometry_unsupported")
    points: list[list[float]] = []
    for point in value:
        if not isinstance(point, list) or len(point) != 2 or not all(_is_finite_number(item) for item in point):
            raise ValueError("geometry_invalid")
        x, y = float(point[0]), float(point[1])
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            raise ValueError("geometry_invalid")
        points.append([x, y])
    area = sum(
        points[index][0] * points[(index + 1) % len(points)][1]
        - points[(index + 1) % len(points)][0] * points[index][1]
        for index in range(len(points))
    )
    if abs(area) <= 0.0:
        raise ValueError("geometry_invalid")
    return points


def _resolve_host_observation(
    *,
    frame: dict[str, Any],
    identity: dict[str, Any],
) -> dict[str, Any]:
    cycle = frame.get("cycle") if isinstance(frame.get("cycle"), dict) else {}
    authority = cycle.get("authority") if isinstance(cycle, dict) else None
    envelope = authority.get("host_application") if isinstance(authority, dict) else None
    if not isinstance(envelope, dict):
        return {"status": "unavailable", "reason": "host_observation_unsupported", "value": None}
    if envelope.get("status") != "ready":
        return {
            "status": "unavailable",
            "reason": str(envelope.get("reason") or "host_observation_unavailable"),
            "value": None,
        }
    value = envelope.get("value")
    try:
        _validate_host_value(value=value, identity=identity, frame=frame, updated_at_ms=envelope.get("updated_at_ms"))
    except ValueError as exc:
        return {"status": "unavailable", "reason": str(exc), "value": None}
    return {"status": "available", "reason": None, "value": deepcopy(value)}


def _validate_host_value(
    *,
    value: Any,
    identity: dict[str, Any],
    frame: dict[str, Any],
    updated_at_ms: Any,
) -> None:
    required = {
        "schema",
        "identity",
        "frame_id",
        "frame_index",
        "observed_at_ms",
        "observer",
        "mode",
        "user_input",
        "pilot_output",
        "host_output",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("host_observation_unsupported")
    if value.get("schema") != HOST_OBSERVATION_SCHEMA:
        raise ValueError("host_observation_unsupported")
    if value.get("identity") != identity:
        raise ValueError("host_observation_generation_mismatch")
    if value.get("frame_id") != frame.get("frame_id") or value.get("frame_index") != frame.get("frame_index"):
        raise ValueError("host_observation_frame_mismatch")
    observed_at_ms = value.get("observed_at_ms")
    if not _is_nonnegative_int(observed_at_ms) or not _is_nonnegative_int(updated_at_ms) or observed_at_ms != updated_at_ms:
        raise ValueError("host_observation_timestamp_mismatch")
    source_timestamp = frame.get("timestamp_ms")
    published_at = frame.get("published_at_ms")
    if not _is_nonnegative_int(source_timestamp) or not _is_nonnegative_int(published_at) or not source_timestamp <= observed_at_ms <= published_at:
        raise ValueError("host_observation_timestamp_mismatch")
    if not isinstance(value.get("observer"), str) or not value["observer"]:
        raise ValueError("host_observation_malformed")
    if value.get("mode") is not None and not isinstance(value.get("mode"), str):
        raise ValueError("host_observation_malformed")
    for field in ("user_input", "pilot_output", "host_output"):
        _validate_control_value(value.get(field), field=field, allow_none=field != "host_output")


def _validate_control_value(value: Any, *, field: str, allow_none: bool) -> None:
    if value is None:
        if allow_none:
            return
        raise ValueError("host_observation_malformed")
    if not isinstance(value, dict) or set(value) != {"steering", "throttle"}:
        raise ValueError("host_observation_malformed")
    if not _is_finite_number(value.get("steering")) or not _is_finite_number(value.get("throttle")):
        raise ValueError("host_observation_malformed")


def _first_view_reason(
    current_image: dict[str, Any],
    evidence: list[dict[str, Any]],
    host_observation: dict[str, Any],
) -> str | None:
    if current_image.get("status") != "available":
        return str(current_image.get("reason") or "source_unavailable")
    for entry in evidence:
        if entry.get("status") != "available":
            return str(entry.get("reason") or "source_unavailable")
    if host_observation.get("status") != "available":
        return str(host_observation.get("reason") or "host_observation_unavailable")
    return None


def _is_loopback_url(url: object) -> bool:
    if not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url)
        return (
            parsed.scheme == "http"
            and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
            and parsed.username is None
            and parsed.password is None
            and parsed.path in {"", "/"}
            and parsed.params == ""
            and parsed.query == ""
            and parsed.fragment == ""
            and parsed.port is not None
        )
    except ValueError:
        return False


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def _bounded_response_body(response: Any, *, deadline: float) -> bytes:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("decision view probe exceeded its total budget")
    body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise ValueError("view_payload_too_large")
    return body


def probe_decision_view(
    *,
    automation_dir: Path,
    vehicle_id: str,
    activation: dict[str, Any],
    timeout_s: float = 0.25,
) -> dict[str, Any]:
    """Probe only the verified loopback origin before exposing an info URL."""

    record_path = Path(automation_dir) / "perception_view.json"
    try:
        raw = record_path.read_bytes()
    except OSError:
        return _probe_unavailable("producer_unavailable")
    if len(raw) > MAX_RECORD_BYTES:
        return _probe_unavailable("view_payload_too_large")
    try:
        record = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _probe_unavailable("view_unreachable")
    if not isinstance(record, dict):
        return _probe_unavailable("view_unreachable")
    if record.get("schema") != "automa_perception_view_v1" or record.get("vehicle_id") != vehicle_id:
        return _probe_unavailable("view_unreachable")
    origin = record.get("url")
    if not _is_loopback_url(origin):
        return _probe_unavailable("view_unreachable")
    run_id = record.get("run_id")
    worker_pid = record.get("worker_pid")
    if type(run_id) is not str or not run_id or not _is_nonnegative_int(worker_pid) or worker_pid <= 0:
        return _probe_unavailable("producer_unavailable")
    try:
        identity = identity_for_activation(
            vehicle_id=vehicle_id,
            run_id=run_id,
            worker_pid=worker_pid,
            activation=activation,
        )
    except (TypeError, ValueError):
        return _probe_unavailable("activation_invalid")
    generation_id = generation_for_identity(identity)
    api_url = urljoin(str(origin).rstrip("/") + "/", DECISION_API_PATH.lstrip("/"))
    api_url = f"{api_url}?{urlencode({'generation': generation_id})}"
    deadline = time.monotonic() + max(0.01, min(float(timeout_s), 0.25))
    request = Request(api_url, method="GET", headers={"Accept": "application/json"})
    opener = build_opener(_NoRedirect())
    response_payload: dict[str, Any] | None = None
    response_status: int | None = None
    try:
        with opener.open(request, timeout=max(0.01, deadline - time.monotonic())) as response:
            response_status = int(getattr(response, "status", 200))
            body = _bounded_response_body(response, deadline=deadline)
            decoded = json.loads(body.decode("utf-8"))
            response_payload = decoded if isinstance(decoded, dict) else None
    except HTTPError as exc:
        response_status = int(exc.code)
        try:
            body = _bounded_response_body(exc, deadline=deadline)
            decoded = json.loads(body.decode("utf-8"))
            response_payload = decoded if isinstance(decoded, dict) else None
        except (OSError, TimeoutError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
            response_payload = None
    except (OSError, URLError, TimeoutError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return _probe_unavailable("view_unreachable")
    if not isinstance(response_payload, dict) or response_payload.get("schema") != DECISION_VIEW_SCHEMA:
        return _probe_unavailable("view_unreachable")
    if response_payload.get("generation_id") not in (None, generation_id):
        return _probe_unavailable("generation_mismatch")
    if response_payload.get("identity") not in (None, identity):
        return _probe_unavailable("generation_mismatch")
    if response_status == 200 and response_payload.get("status") in {"current", "partial"} and isinstance(response_payload.get("decision"), dict):
        page_url = urljoin(str(origin).rstrip("/") + "/", DECISION_VIEW_PATH.lstrip("/"))
        page_url = f"{page_url}?{urlencode({'generation': generation_id})}"
        return {
            "available": True,
            "status": response_payload.get("status"),
            "reason": response_payload.get("reason"),
            "url": page_url,
            "api_url": api_url,
            "generation_id": generation_id,
            "identity": identity,
        }
    reason = response_payload.get("reason") if isinstance(response_payload.get("reason"), str) else None
    return {
        "available": False,
        "status": "unavailable",
        "reason": reason or _probe_reason_for_status(response_status),
        "url": None,
        "api_url": None,
        "generation_id": generation_id if response_payload.get("generation_id") == generation_id else None,
        "identity": identity if response_payload.get("identity") == identity else None,
    }


def _probe_reason_for_status(status: int | None) -> str:
    if status in {409}:
        return "generation_mismatch"
    if status == 422:
        return "decision_invalid"
    if status == 503:
        return "producer_unavailable"
    return "view_unreachable"


def _probe_unavailable(reason: str) -> dict[str, Any]:
    return {
        "available": False,
        "status": "unavailable",
        "reason": reason,
        "url": None,
        "api_url": None,
        "generation_id": None,
        "identity": None,
    }


__all__ = [
    "DECISION_API_PATH",
    "DECISION_IMAGE_PATH",
    "DECISION_VIEW_ID",
    "DECISION_VIEW_PATH",
    "DECISION_VIEW_SCHEMA",
    "DecisionViewHTTPError",
    "DecisionViewPublisher",
    "HOST_OBSERVATION_SCHEMA",
    "activation_sha256",
    "default_limits",
    "generation_for_identity",
    "identity_for_activation",
    "parse_generation_query",
    "parse_image_id",
    "probe_decision_view",
]
