"""Loopback perception-memory workbench for deterministic image replays.

The workbench is deliberately a thin presentation and lifecycle boundary. It
normalizes one ordered image-directory source, feeds it through the existing
perception, observation, and memory seams, and exposes the resulting state to
both the CLI and a small loopback HTTP page.
"""

from __future__ import annotations

import copy
import hashlib
import json
import mimetypes
import os
import re
import threading
import time
import uuid
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, TextIO
from urllib.parse import parse_qs, quote, urlparse

from autonomy.decision import (
    ActivatedMemoryStage,
    DecisionCycle,
    DecisionFrameContext,
    DecisionStages,
    MemoryActivation,
    Observation,
    observation_from_perception,
)
from autonomy.decision.activation import bounds_from_config
from autonomy.perception import (
    PerceptionMapper,
    PerceptionText,
    build_perception_request,
)
from autonomy.perception.activation import instantiate_perception_mapper
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorReading, SensorSnapshot
from implementations.memory.catalog import (
    DEFAULT_MEMORY_IMPLEMENTATION,
    build_memory_activation_payload,
)
from implementations.perception.catalog import (
    DEFAULT_PERCEPTION_ALGORITHM,
    PERCEPTION_ALGORITHMS,
)

from .perception_runs import CommandResult


WORKBENCH_SEQUENCE_ID = "workbench.image_replay.v1"
WORKBENCH_STATE_SCHEMA = "workbench_image_replay_state_v1"
WORKBENCH_SERVER_SCHEMA = "workbench_server_v1"
WORKBENCH_ACTION_RESULT_SCHEMA = "workbench_action_result_v1"
WORKBENCH_ERROR_SCHEMA = "workbench_error_v1"
WORKBENCH_HOST = "127.0.0.1"
WORKBENCH_DEFAULT_CADENCE_MS = 250
WORKBENCH_DEFAULT_MAX_FRAMES = 256
WORKBENCH_DEFAULT_MAX_IMAGE_BYTES = 32 * 1024 * 1024
WORKBENCH_MAX_ACTION_BYTES = 64 * 1024
WORKBENCH_ADAPTER = "image_directory"
WORKBENCH_HTML_PATH = Path(__file__).with_name("workbench.html")
WORKBENCH_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
WORKBENCH_UNSUPPORTED_IMAGE_EXTENSIONS = {
    ".gif",
    ".tif",
    ".tiff",
    ".heic",
    ".heif",
    ".avif",
}
WORKBENCH_PHASES = ("idle", "running", "paused", "completed", "failed", "cancelled")
WORKBENCH_ACTIONS = (
    "validate",
    "start",
    "pause",
    "resume",
    "step",
    "cancel",
    "reset",
    "set_cadence",
)
_SOURCE_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_MANIFEST_NAMES = (
    "manifest.json",
    "workbench.json",
    "sequence.json",
    "run.json",
    "report.json",
)


class SourceValidationError(ValueError):
    """A user-visible source contract failure."""

    boundary = "source"


class ReplayActionError(ValueError):
    """A structured action or lifecycle boundary failure."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 409,
        boundary: str = "action",
        state: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.boundary = boundary
        self.state = state


@dataclass(frozen=True)
class ReplayFrame:
    source_id: str
    frame_id: str
    frame_index: int
    position: int
    timestamp_ms: int
    image_path: Path | None
    absence_reason: str | None = None
    content_type: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def absent(self) -> bool:
        return self.image_path is None

    def to_dict(self, *, include_path: bool = True) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "frame_id": self.frame_id,
            "frame_index": self.frame_index,
            "position": self.position,
            "timestamp_ms": self.timestamp_ms,
            "image_path": str(self.image_path) if include_path and self.image_path else None,
            "image_name": self.image_path.name if self.image_path else None,
            "absence_reason": self.absence_reason,
            "absent": self.absent,
            "content_type": self.content_type,
            "metadata": copy.deepcopy(self.metadata),
        }


@dataclass(frozen=True)
class ImageFeed:
    source_path: Path
    source_id: str
    frames: tuple[ReplayFrame, ...]
    manifest_path: Path | None = None
    adapter: str = WORKBENCH_ADAPTER

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter": self.adapter,
            "source_path": str(self.source_path),
            "source_id": self.source_id,
            "manifest_path": str(self.manifest_path) if self.manifest_path else None,
            "frame_count": len(self.frames),
            "frames": [frame.to_dict() for frame in self.frames],
        }


def normalize_image_directory(
    source_dir: str | os.PathLike[str],
    *,
    source_root: Path | None = None,
    max_frames: int = WORKBENCH_DEFAULT_MAX_FRAMES,
    max_image_bytes: int = WORKBENCH_DEFAULT_MAX_IMAGE_BYTES,
) -> ImageFeed:
    """Validate and normalize the bounded image-directory source contract."""

    if not isinstance(source_dir, (str, os.PathLike)):
        raise SourceValidationError("source_dir must be a path string")
    raw_source = os.fspath(source_dir)
    if "\x00" in raw_source:
        raise SourceValidationError("source_dir contains a NUL byte")
    if max_frames <= 0:
        raise SourceValidationError("max_frames must be greater than zero")
    if max_image_bytes <= 0:
        raise SourceValidationError("max_image_bytes must be greater than zero")

    root = Path(source_root).expanduser().resolve() if source_root is not None else None
    candidate = Path(raw_source).expanduser()
    if root is not None and not candidate.is_absolute():
        candidate = root / candidate
    source_path = candidate.resolve()
    if root is not None and not _is_within(source_path, root):
        raise SourceValidationError("source_dir must remain inside the configured source root")
    if candidate.is_symlink() or source_path.is_symlink():
        raise SourceValidationError("source_dir may not be a symlink")
    if not source_path.exists():
        raise SourceValidationError(f"source directory does not exist: {source_path}")
    if not source_path.is_dir():
        raise SourceValidationError(f"source path is not a directory: {source_path}")

    manifest_path, manifest = _find_manifest(source_path)
    source_id = _source_id(source_path, manifest)
    entries = _manifest_entries(manifest) if manifest is not None else None
    if entries is None:
        paths = _lexical_image_paths(source_path)
        if not paths:
            raise SourceValidationError(f"no supported images found under {source_path}")
        if len(paths) > max_frames:
            raise SourceValidationError(
                f"source contains {len(paths)} images; max_frames is {max_frames}"
            )
        frames = tuple(
            _build_frame(
                source_id=source_id,
                position=position,
                entry={"image_path": str(path.relative_to(source_path))},
                source_path=source_path,
                max_image_bytes=max_image_bytes,
            )
            for position, path in enumerate(paths)
        )
    else:
        if not entries:
            raise SourceValidationError("source manifest contains no frames")
        if len(entries) > max_frames:
            raise SourceValidationError(
                f"source manifest contains {len(entries)} frames; max_frames is {max_frames}"
            )
        frames = tuple(
            _build_frame(
                source_id=source_id,
                position=position,
                entry=entry,
                source_path=source_path,
                max_image_bytes=max_image_bytes,
            )
            for position, entry in enumerate(entries)
        )

    _validate_frame_sequence(frames)
    return ImageFeed(
        source_path=source_path,
        source_id=source_id,
        frames=frames,
        manifest_path=manifest_path,
    )


load_image_feed = normalize_image_directory


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _path_contains_symlink(path: Path, root: Path) -> bool:
    """Return whether a path or one of its components is a symlink."""

    current = path
    while True:
        if current.is_symlink():
            return True
        if current == root:
            return False
        try:
            current.relative_to(root)
        except ValueError:
            return False
        current = current.parent


def _source_id(source_path: Path, manifest: dict[str, Any] | None = None) -> str:
    declared: Any = None
    if manifest:
        declared = manifest.get("source_id")
        if declared is None and isinstance(manifest.get("source"), dict):
            declared = manifest["source"].get("source_id")
    if declared is not None:
        if not isinstance(declared, str):
            raise SourceValidationError("source_id must be a string")
        value = str(declared).strip()
        if not _SOURCE_ID_RE.fullmatch(value):
            raise SourceValidationError(
                "source_id must contain only letters, numbers, underscore, dot, colon, or hyphen"
            )
        return value
    name = re.sub(r"[^A-Za-z0-9_.-]+", "-", source_path.name).strip("-") or "source"
    digest = hashlib.sha256(str(source_path).encode("utf-8")).hexdigest()[:12]
    return f"image-directory:{name[:80]}:{digest}"


def _find_manifest(source_path: Path) -> tuple[Path | None, dict[str, Any] | None]:
    for name in _MANIFEST_NAMES:
        path = source_path / name
        if not os.path.lexists(path):
            continue
        if path.is_symlink() or not path.is_file():
            raise SourceValidationError(f"manifest is not a regular file: {path.name}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise SourceValidationError(f"could not read manifest {path.name}: {exc}") from exc
        if not isinstance(payload, dict):
            raise SourceValidationError(f"manifest {path.name} must contain a JSON object")
        return path, payload
    return None, None


def _manifest_entries(manifest: dict[str, Any] | None) -> list[Any] | None:
    if manifest is None:
        return None
    for key in ("frames", "images", "captures"):
        entries = manifest.get(key)
        if isinstance(entries, list):
            return entries
    sequence = manifest.get("sequence")
    if isinstance(sequence, dict) and isinstance(sequence.get("frames"), list):
        return sequence["frames"]
    return None


def _lexical_image_paths(source_path: Path) -> list[Path]:
    def supported_files(directory: Path) -> list[Path]:
        paths: list[Path] = []
        for path in sorted(directory.iterdir(), key=lambda item: item.name.casefold()):
            if path.name in _MANIFEST_NAMES:
                continue
            suffix = path.suffix.lower()
            if suffix in WORKBENCH_UNSUPPORTED_IMAGE_EXTENSIONS:
                raise SourceValidationError(f"unsupported image format: {path.name}")
            if suffix not in WORKBENCH_IMAGE_EXTENSIONS:
                continue
            if path.is_symlink():
                raise SourceValidationError(f"image may not be a symlink: {path.name}")
            if not path.is_file():
                continue
            paths.append(path)
        return paths

    paths = supported_files(source_path)
    if paths:
        return paths
    frames_dir = source_path / "frames"
    if frames_dir.exists():
        if frames_dir.is_symlink() or not frames_dir.is_dir():
            raise SourceValidationError("source frames path must be a regular directory")
        return supported_files(frames_dir)
    return []


def _build_frame(
    *,
    source_id: str,
    position: int,
    entry: Any,
    source_path: Path,
    max_image_bytes: int,
) -> ReplayFrame:
    if isinstance(entry, str):
        entry = {"image_path": entry}
    if not isinstance(entry, dict):
        raise SourceValidationError(f"manifest frame {position} must be a string or object")

    absence_reason = _absence_reason(entry)
    raw_image = _first_value(
        entry,
        "image_path",
        "path",
        "image",
        "file",
        "filename",
        "source_path",
    )
    image_path: Path | None = None
    image_metadata: dict[str, Any] = {}
    if raw_image is not None and str(raw_image).strip():
        relative = Path(str(raw_image))
        if relative.is_absolute() or ".." in relative.parts:
            raise SourceValidationError(
                f"manifest frame {position} image path must be relative to the source"
            )
        raw_image_path = source_path / relative
        if _path_contains_symlink(raw_image_path, source_path):
            raise SourceValidationError(
                f"manifest frame {position} image path may not traverse symlinks"
            )
        image_path = raw_image_path.resolve()
        if not _is_within(image_path, source_path):
            raise SourceValidationError(f"manifest frame {position} image escapes the source")
        image_metadata = _validate_image_path(image_path, max_image_bytes=max_image_bytes)
    elif absence_reason is None:
        raise SourceValidationError(f"manifest frame {position} has no image or absence reason")

    frame_id = str(
        _first_value(entry, "frame_id", "id", "frame")
        or f"{source_id}:frame_{position:06d}"
    ).strip()
    if not frame_id or len(frame_id) > 160 or any(char.isspace() for char in frame_id):
        raise SourceValidationError(f"manifest frame {position} has an invalid frame_id")
    frame_index = _nonnegative_int(
        _first_value(entry, "frame_index", "sequence_index", "index"),
        default=position,
        label=f"frame {position} frame_index",
    )
    timestamp_ms = _nonnegative_int(
        _first_value(entry, "timestamp_ms", "captured_at_ms", "timestamp"),
        default=position * 1000,
        label=f"frame {position} timestamp_ms",
    )
    metadata = {
        "manifest_position": position,
        **image_metadata,
    }
    for key in ("dropout", "absence", "annotation", "label"):
        if key in entry and key not in metadata:
            metadata[key] = copy.deepcopy(entry[key])
    return ReplayFrame(
        source_id=source_id,
        frame_id=frame_id,
        frame_index=frame_index,
        position=position,
        timestamp_ms=timestamp_ms,
        image_path=image_path,
        absence_reason=absence_reason,
        content_type=_content_type(image_path) if image_path else None,
        metadata=metadata,
    )


def _first_value(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _absence_reason(entry: dict[str, Any]) -> str | None:
    for key in ("absence_reason", "dropout_reason", "absence"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:240]
        if isinstance(value, dict):
            reason = value.get("reason") or value.get("message")
            if reason:
                return str(reason).strip()[:240]
    for key in ("absent", "dropout"):
        value = entry.get(key)
        if value is True:
            return key
        if isinstance(value, str) and value.strip():
            return value.strip()[:240]
        if isinstance(value, dict):
            reason = value.get("reason") or value.get("message")
            if reason:
                return str(reason).strip()[:240]
    if entry.get("absence") is True:
        return "absence"
    return None


def _nonnegative_int(value: Any, *, default: int, label: str) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise SourceValidationError(f"{label} must be a nonnegative integer")
    if isinstance(value, float) and not value.is_integer():
        raise SourceValidationError(f"{label} must be a nonnegative integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise SourceValidationError(f"{label} must be a nonnegative integer") from exc
    if number < 0:
        raise SourceValidationError(f"{label} must be a nonnegative integer")
    return number


def _validate_image_path(path: Path, *, max_image_bytes: int) -> dict[str, Any]:
    if path.is_symlink():
        raise SourceValidationError(f"image may not be a symlink: {path.name}")
    if not path.exists() or not path.is_file():
        raise SourceValidationError(f"manifest image does not exist: {path.name}")
    if path.suffix.lower() not in WORKBENCH_IMAGE_EXTENSIONS:
        raise SourceValidationError(f"unsupported image format: {path.name}")
    size = path.stat().st_size
    if size <= 0:
        raise SourceValidationError(f"image is empty: {path.name}")
    if size > max_image_bytes:
        raise SourceValidationError(
            f"image {path.name} is {size} bytes; max_image_bytes is {max_image_bytes}"
        )
    try:
        from PIL import Image

        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            width, height = image.size
            image_format = image.format
        expected_formats = {
            ".jpg": "jpeg",
            ".jpeg": "jpeg",
            ".png": "png",
            ".webp": "webp",
            ".bmp": "bmp",
        }
        decoded_format = str(image_format or "").lower()
        if decoded_format != expected_formats.get(path.suffix.lower()):
            raise SourceValidationError(
                f"image {path.name} has unsupported decoded format {image_format!r}"
            )
    except Exception as exc:  # noqa: BLE001 - source validation boundary
        raise SourceValidationError(f"image {path.name} is not decodable: {exc}") from exc
    return {
        "bytes": size,
        "width_px": int(width),
        "height_px": int(height),
        "format": str(image_format or "").lower() or None,
    }


def _validate_frame_sequence(frames: tuple[ReplayFrame, ...]) -> None:
    if not frames:
        raise SourceValidationError("normalized source contains no frames")
    ids = [frame.frame_id for frame in frames]
    if len(ids) != len(set(ids)):
        raise SourceValidationError("source contains duplicate frame_id values")
    indices = [frame.frame_index for frame in frames]
    timestamps = [frame.timestamp_ms for frame in frames]
    if any(current <= previous for previous, current in zip(indices, indices[1:])):
        raise SourceValidationError("frame_index values must be strictly increasing")
    if any(current <= previous for previous, current in zip(timestamps, timestamps[1:])):
        raise SourceValidationError("timestamp_ms values must be strictly increasing")


def _content_type(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _snapshot_for_frame(frame: ReplayFrame) -> SensorSnapshot | None:
    if frame.absent or frame.image_path is None:
        return None
    reading = SensorReading(
        sensor_id=FRONT_CAMERA_SENSOR_ID,
        sensor_kind="camera",
        captured_at_ms=frame.timestamp_ms,
        path=str(frame.image_path),
        metadata={
            "source_id": frame.source_id,
            "frame_id": frame.frame_id,
            "sequence_index": frame.position,
        },
    )
    return SensorSnapshot(
        read_id=frame.frame_id,
        readings={FRONT_CAMERA_SENSOR_ID: reading},
        started_at_ms=frame.timestamp_ms,
        completed_at_ms=frame.timestamp_ms,
        request={
            "source": "workbench.image_replay.v1",
            "requested_sensors": [FRONT_CAMERA_SENSOR_ID],
        },
        metadata={
            "source_id": frame.source_id,
            "sequence_index": frame.position,
            "absence": False,
        },
    )


def _default_mapper() -> PerceptionMapper:
    config = PERCEPTION_ALGORITHMS[DEFAULT_PERCEPTION_ALGORITHM]
    return instantiate_perception_mapper(
        str(config["mapper_spec"]),
        copy.deepcopy(dict(config["mapper_config"])),
    )


def _default_memory_stage() -> ActivatedMemoryStage:
    payload = build_memory_activation_payload(DEFAULT_MEMORY_IMPLEMENTATION)
    section = payload["memory"]
    config = copy.deepcopy(dict(section["implementation_config"]))
    activation = MemoryActivation(
        implementation_id=str(section["implementation_id"]),
        implementation_spec=str(section["implementation_spec"]),
        implementation_config=config,
        bounds=bounds_from_config(config),
        source_path=Path("workbench-fixed-memory"),
        payload=payload,
    )
    return ActivatedMemoryStage(activation)


def _safe_status(value: Any) -> str:
    return str(value or "").strip().lower()


def _state_action_set(phase: str, *, feed: ImageFeed | None) -> list[str]:
    if phase == "idle":
        return ["validate", "start", "reset"] if feed is None else ["validate", "start", "reset"]
    if phase == "running":
        return ["pause", "cancel", "reset", "set_cadence"]
    if phase == "paused":
        return ["resume", "step", "cancel", "reset", "set_cadence"]
    if phase in {"completed", "failed", "cancelled"}:
        return ["validate", "start", "reset"]
    return []


class ImageReplayRunner:
    """Shared replay owner used by both the CLI and loopback API."""

    def __init__(
        self,
        source_dir: str | os.PathLike[str] | None = None,
        *,
        source_root: Path | None = None,
        cadence_ms: int = WORKBENCH_DEFAULT_CADENCE_MS,
        max_frames: int = WORKBENCH_DEFAULT_MAX_FRAMES,
        max_image_bytes: int = WORKBENCH_DEFAULT_MAX_IMAGE_BYTES,
        mapper_factory: Callable[[], PerceptionMapper] | None = None,
        memory_stage_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.source_root = Path(source_root).expanduser().resolve() if source_root else None
        self.source_dir = os.fspath(source_dir) if source_dir is not None else None
        self.max_frames = int(max_frames)
        self.max_image_bytes = int(max_image_bytes)
        self.mapper_factory = mapper_factory or _default_mapper
        self.memory_stage_factory = memory_stage_factory or _default_memory_stage
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._action_lock = threading.RLock()
        self._worker: threading.Thread | None = None
        self._feed: ImageFeed | None = None
        self._mapper: Any = None
        self._memory_stage: Any = None
        self._generation = 0
        self._server: WorkbenchServer | None = None
        self._cadence_ms = self._validate_cadence(cadence_ms)
        self._server_identity = f"workbench-{uuid.uuid4().hex[:12]}"
        self._state: dict[str, Any] = {
            "schema": WORKBENCH_STATE_SCHEMA,
            "server_identity": self._server_identity,
            "sequence_id": WORKBENCH_SEQUENCE_ID,
            "run_id": None,
            "phase": "idle",
            "source": None,
            "source_identity": None,
            "adapter": WORKBENCH_ADAPTER,
            "current_frame": None,
            "position": 0,
            "progress": {"completed": 0, "total": 0, "percent": 0.0},
            "summary": self._summary(),
            "machine_detail": self._machine_detail(),
            "perception": None,
            "observation": None,
            "memory": None,
            "timeline": [],
            "failure": None,
            "failure_boundary": None,
            "recovery_action": "start",
            "cleanup": None,
            "controls": self._controls(phase="idle"),
            "last_action": None,
        }

    @property
    def server_identity(self) -> str:
        return self._server_identity

    def attach_server(self, server: WorkbenchServer) -> None:
        with self._lock:
            self._server = server

    def detach_server(self) -> None:
        with self._lock:
            self._server = None

    def state(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._state)

    def frame_bytes(self, frame_id: str | None = None) -> tuple[bytes, str] | None:
        with self._lock:
            frame = self._frame_for_id_locked(frame_id)
            if frame is None or frame.image_path is None:
                return None
            path = frame.image_path
        try:
            return path.read_bytes(), frame.content_type or _content_type(path)
        except OSError:
            return None

    def _frame_for_id_locked(self, frame_id: str | None) -> ReplayFrame | None:
        if self._feed is None:
            return None
        selected_id = frame_id
        if selected_id is None:
            current = self._state.get("current_frame")
            selected_id = current.get("frame_id") if isinstance(current, dict) else None
        if selected_id is None:
            return None
        return next(
            (frame for frame in self._feed.frames if frame.frame_id == selected_id),
            None,
        )

    def validate_source(self, source_dir: str | os.PathLike[str] | None = None) -> ImageFeed:
        raw_source = self.source_dir if source_dir is None else os.fspath(source_dir)
        if raw_source is None:
            raise SourceValidationError("start requires source_dir")
        with self._lock:
            if self._state["phase"] in {"running", "paused"}:
                raise SourceValidationError("source cannot be changed while replay is active")
        feed = normalize_image_directory(
            raw_source,
            source_root=self.source_root,
            max_frames=self.max_frames,
            max_image_bytes=self.max_image_bytes,
        )
        with self._lock:
            self._feed = feed
            self.source_dir = str(feed.source_path)
            self._state["phase"] = "idle"
            self._state["source"] = feed.to_dict()
            self._state["source_identity"] = feed.source_id
            self._state["adapter"] = feed.adapter
            self._state["current_frame"] = None
            self._state["position"] = 0
            self._state["progress"] = {
                "completed": 0,
                "total": len(feed.frames),
                "percent": 0.0,
            }
            self._state["perception"] = None
            self._state["observation"] = None
            self._state["memory"] = None
            self._state["timeline"] = []
            self._state["failure"] = None
            self._state["failure_boundary"] = None
            self._state["summary"] = self._summary(
                frames_completed=0,
                frames_total=len(feed.frames),
            )
            self._state["controls"] = self._controls()
        return feed

    def start(
        self,
        source_dir: str | os.PathLike[str] | None = None,
        *,
        cadence_ms: int | None = None,
    ) -> dict[str, Any]:
        with self._action_lock:
            with self._condition:
                phase = self._state["phase"]
                if phase in {"running", "paused"}:
                    raise ReplayActionError(
                        f"cannot start while replay is {phase}",
                        boundary="lifecycle",
                    )
                if cadence_ms is not None:
                    self._cadence_ms = self._validate_cadence(cadence_ms)
                raw_source = self.source_dir if source_dir is None else os.fspath(source_dir)
                if raw_source is None:
                    raise ReplayActionError(
                        "start requires source_dir",
                        status_code=400,
                        boundary="source",
                    )
                run_id = f"run-{uuid.uuid4().hex}"
                self._generation += 1
                generation = self._generation
                if self._mapper is not None or self._memory_stage is not None:
                    self._cleanup_locked()
                self._feed = None
                self._mapper = None
                self._memory_stage = None
                self._state = self._fresh_run_state(run_id)
                self._state["source"] = {"path": str(raw_source)}
                try:
                    feed = normalize_image_directory(
                        raw_source,
                        source_root=self.source_root,
                        max_frames=self.max_frames,
                        max_image_bytes=self.max_image_bytes,
                    )
                    mapper = self.mapper_factory()
                    memory_stage = self.memory_stage_factory()
                except Exception as exc:  # noqa: BLE001 - startup isolation boundary
                    self._set_failure_locked(
                        boundary=getattr(exc, "boundary", "startup"),
                        message=str(exc),
                        recovery_action="start",
                    )
                    self._condition.notify_all()
                    return copy.deepcopy(self._state)
                self._feed = feed
                self.source_dir = str(feed.source_path)
                self._mapper = mapper
                self._memory_stage = memory_stage
                self._state["source"] = feed.to_dict()
                self._state["source_identity"] = feed.source_id
                self._state["adapter"] = feed.adapter
                self._state["progress"]["total"] = len(feed.frames)
                self._state["machine_detail"] = self._machine_detail()
                self._state["phase"] = "running"
                self._state["controls"] = self._controls()
                self._state["last_action"] = {
                    "action": "start",
                    "run_id": run_id,
                    "at_ms": _now_ms(),
                }
                self._worker = threading.Thread(
                    target=self._run_loop,
                    args=(run_id, generation),
                    name=f"automa-workbench-{run_id[-12:]}",
                    daemon=True,
                )
                self._worker.start()
                self._condition.notify_all()
                return copy.deepcopy(self._state)

    def wait(self, timeout: float | None = None) -> dict[str, Any]:
        worker = self._worker
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=timeout)
        return self.state()

    def dispatch(
        self,
        action: str,
        *,
        run_id: str | None = None,
        source_dir: str | os.PathLike[str] | None = None,
        cadence_ms: int | None = None,
    ) -> dict[str, Any]:
        action = str(action or "").strip()
        if action not in WORKBENCH_ACTIONS:
            raise ReplayActionError(
                f"unknown workbench action {action!r}",
                status_code=400,
                boundary="action",
            )
        with self._action_lock:
            with self._lock:
                self._check_run_id_locked(action, run_id)
            if action == "validate":
                with self._lock:
                    if self._state["phase"] in {"running", "paused"}:
                        raise ReplayActionError(
                            "validate is unavailable while replay is active",
                            boundary="lifecycle",
                        )
                try:
                    self.validate_source(source_dir)
                except SourceValidationError as exc:
                    with self._lock:
                        self._state["failure"] = {
                            "message": str(exc),
                            "boundary": "source",
                        }
                        self._state["failure_boundary"] = "source"
                        self._state["last_action"] = {"action": action, "at_ms": _now_ms()}
                        self._state["controls"] = self._controls()
                    raise ReplayActionError(
                        str(exc),
                        status_code=422,
                        boundary="source",
                        state=self.state(),
                    ) from exc
                with self._lock:
                    self._state["last_action"] = {"action": action, "at_ms": _now_ms()}
                    self._state["failure"] = None
                    self._state["failure_boundary"] = None
                return self.state()
            if action == "start":
                return self.start(source_dir, cadence_ms=cadence_ms)
            if action == "set_cadence":
                if cadence_ms is None:
                    raise ReplayActionError(
                        "set_cadence requires cadence_ms",
                        status_code=400,
                        boundary="input",
                    )
                value = self._validate_cadence(cadence_ms)
                with self._lock:
                    if self._state["phase"] not in {"running", "paused"}:
                        raise ReplayActionError(
                            "cadence can only change while replay is running or paused",
                            boundary="lifecycle",
                        )
                    self._cadence_ms = value
                    self._state["controls"] = self._controls()
                    self._state["last_action"] = {
                        "action": action,
                        "cadence_ms": value,
                        "at_ms": _now_ms(),
                    }
                    self._condition.notify_all()
                return self.state()
            if action == "pause":
                return self._pause()
            if action == "resume":
                return self._resume()
            if action == "step":
                return self._step()
            if action == "cancel":
                return self._cancel()
            if action == "reset":
                return self._reset()
        raise AssertionError(f"unhandled action {action}")

    def _pause(self) -> dict[str, Any]:
        with self._condition:
            if self._state["phase"] != "running":
                raise ReplayActionError("pause requires a running replay", boundary="lifecycle")
            self._state["phase"] = "paused"
            self._record_action_locked("pause")
            self._condition.notify_all()
            return copy.deepcopy(self._state)

    def _resume(self) -> dict[str, Any]:
        with self._condition:
            if self._state["phase"] != "paused":
                raise ReplayActionError("resume requires a paused replay", boundary="lifecycle")
            self._state["phase"] = "running"
            self._record_action_locked("resume")
            self._condition.notify_all()
            return copy.deepcopy(self._state)

    def _step(self) -> dict[str, Any]:
        with self._condition:
            if self._state["phase"] != "paused" or self._feed is None:
                raise ReplayActionError("step requires a paused replay", boundary="lifecycle")
            run_id = str(self._state["run_id"])
            generation = self._generation
            if self._state["position"] >= len(self._feed.frames):
                self._complete_locked()
                return copy.deepcopy(self._state)
            frame = self._feed.frames[self._state["position"]]
        self._process_one(run_id, generation, frame, allow_paused=True)
        with self._lock:
            self._record_action_locked("step")
            return copy.deepcopy(self._state)

    def _cancel(self) -> dict[str, Any]:
        with self._condition:
            if self._state["phase"] not in {"running", "paused"}:
                raise ReplayActionError("cancel requires a running or paused replay", boundary="lifecycle")
            self._state["phase"] = "cancelled"
            self._state["recovery_action"] = "start"
            self._state["cleanup"] = self._cleanup_locked()
            self._record_action_locked("cancel")
            self._condition.notify_all()
            return copy.deepcopy(self._state)

    def _reset(self) -> dict[str, Any]:
        with self._condition:
            self._generation += 1
            if self._state["phase"] in {"running", "paused"}:
                self._state["phase"] = "cancelled"
                self._state["cleanup"] = self._cleanup_locked()
            self._feed = None
            self._mapper = None
            self._memory_stage = None
            self._state = self._initial_state()
            self._state["last_action"] = {"action": "reset", "at_ms": _now_ms()}
            self._condition.notify_all()
            return copy.deepcopy(self._state)

    def _run_loop(self, run_id: str, generation: int) -> None:
        while True:
            with self._condition:
                if (
                    generation != self._generation
                    or self._state["run_id"] != run_id
                    or self._state["phase"] not in {"running", "paused"}
                ):
                    return
                if self._state["phase"] == "paused":
                    self._condition.wait()
                    continue
                feed = self._feed
                position = int(self._state["position"])
                if feed is None or position >= len(feed.frames):
                    self._complete_locked()
                    return
                frame = feed.frames[position]
                cadence_ms = self._cadence_ms
            self._process_one(run_id, generation, frame)
            if cadence_ms > 0:
                with self._condition:
                    if (
                        generation != self._generation
                        or self._state["run_id"] != run_id
                        or self._state["phase"] != "running"
                    ):
                        continue
                    self._condition.wait(timeout=cadence_ms / 1000.0)

    def _process_one(
        self,
        run_id: str,
        generation: int,
        frame: ReplayFrame,
        *,
        allow_paused: bool = False,
    ) -> None:
        with self._action_lock:
            with self._lock:
                if (
                    generation != self._generation
                    or self._state["run_id"] != run_id
                    or self._state["phase"] not in ({"running", "paused"} if allow_paused else {"running"})
                ):
                    return
                self._state["current_frame"] = frame.to_dict()
                self._state["position"] = frame.position
                self._state["progress"]["percent"] = (
                    round((frame.position / len(self._feed.frames)) * 100.0, 2)
                    if self._feed
                    else 0.0
                )
                mapper = self._mapper
                memory_stage = self._memory_stage
            try:
                snapshot = _snapshot_for_frame(frame)
                context = DecisionFrameContext(
                    frame_id=frame.frame_id,
                    frame_index=frame.frame_index,
                    timestamp_ms=frame.timestamp_ms,
                    sensor_snapshot=snapshot,
                    mode="workbench_replay",
                    metadata={
                        "source": WORKBENCH_SEQUENCE_ID,
                        "source_id": frame.source_id,
                        "sequence_index": frame.position,
                    },
                )

                def perceive(current: DecisionFrameContext) -> PerceptionText | None:
                    if frame.absent or current.sensor_snapshot is None:
                        return None
                    request = build_perception_request(
                        current.sensor_snapshot,
                        metadata={
                            "source": WORKBENCH_SEQUENCE_ID,
                            "source_id": frame.source_id,
                            "sequence_index": frame.position,
                        },
                    )
                    return mapper.perceive(request)

                def observe(
                    current: DecisionFrameContext,
                    perception: PerceptionText | None,
                ) -> Observation:
                    return observation_from_perception(
                        observation_id=f"{frame.source_id}:{frame.frame_id}",
                        sensor_snapshot=current.sensor_snapshot,
                        perception=perception,
                        metadata={
                            "source": WORKBENCH_SEQUENCE_ID,
                            "source_id": frame.source_id,
                            "sequence_index": frame.position,
                            "absence_reason": frame.absence_reason,
                        },
                        created_at_ms=frame.timestamp_ms,
                    )

                def remember(
                    current: DecisionFrameContext,
                    observation: Observation | None,
                ) -> Any:
                    if callable(memory_stage):
                        return memory_stage(current, observation)
                    return memory_stage.update(current, observation)

                result = DecisionCycle(
                    DecisionStages(
                        perceive=perceive,
                        observe=observe,
                        remember=remember,
                    ),
                    idle_reason="workbench-observation-only",
                ).run(context)
                perception_payload = result.perception.to_dict() if result.perception else None
                observation_payload = result.observation.to_dict() if result.observation else None
                memory_payload = result.memory.to_dict() if result.memory else None
                with self._condition:
                    previous_memory = self._state.get("memory")
                    self._state["perception"] = perception_payload
                    self._state["observation"] = observation_payload
                    self._state["memory"] = memory_payload
                    self._state["progress"]["completed"] = frame.position + 1
                    self._state["progress"]["percent"] = round(
                        ((frame.position + 1) / len(self._feed.frames)) * 100.0,
                        2,
                    ) if self._feed else 100.0
                    self._state["summary"] = self._summary(
                        perception=result.perception,
                        observation=result.observation,
                        memory=result.memory,
                        duration_ms=result.duration_ms,
                    )
                    self._state["timeline"].append(
                        self._timeline_item(
                            frame=frame,
                            result=result,
                            previous_memory=previous_memory,
                        )
                    )
                    self._state["position"] = frame.position + 1
                    if result.perception is not None and _safe_status(result.perception.status) in {
                        "error",
                        "unavailable",
                    }:
                        self._set_failure_locked(
                            boundary="perception",
                            message=f"perception status is {result.perception.status}",
                            recovery_action="start",
                        )
                    elif result.memory is not None and _safe_status(result.memory.health) == "error":
                        self._set_failure_locked(
                            boundary="memory",
                            message=result.memory.error or "memory stage returned an error",
                            recovery_action="start",
                        )
                    elif self._state["position"] >= len(self._feed.frames):
                        self._complete_locked()
                    self._condition.notify_all()
            except Exception as exc:  # noqa: BLE001 - per-frame isolation boundary
                with self._condition:
                    self._set_failure_locked(
                        boundary=getattr(exc, "boundary", "pipeline"),
                        message=f"{type(exc).__name__}: {exc}",
                        recovery_action="start",
                    )
                    self._condition.notify_all()

    def _complete_locked(self) -> None:
        if self._state["phase"] in {"running", "paused"}:
            self._state["phase"] = "completed"
            self._state["progress"]["completed"] = self._state["progress"]["total"]
            self._state["progress"]["percent"] = 100.0
            self._state["cleanup"] = self._cleanup_locked()
            self._record_action_locked("complete")
            self._condition.notify_all()

    def _set_failure_locked(
        self,
        *,
        boundary: str,
        message: str,
        recovery_action: str,
    ) -> None:
        self._state["phase"] = "failed"
        self._state["failure"] = {
            "message": str(message)[:1000],
            "boundary": str(boundary),
        }
        self._state["failure_boundary"] = str(boundary)
        self._state["recovery_action"] = recovery_action
        self._state["cleanup"] = self._cleanup_locked()
        self._state["controls"] = self._controls()

    def _cleanup_locked(self) -> dict[str, Any]:
        mapper_status = "not_created"
        memory_status = "not_created"
        if self._mapper is not None:
            try:
                reset = getattr(self._mapper, "reset", None)
                if callable(reset):
                    reset()
                close = getattr(self._mapper, "close", None)
                if callable(close):
                    close()
                mapper_status = "reset"
            except Exception as exc:  # noqa: BLE001 - cleanup boundary
                mapper_status = f"error: {type(exc).__name__}: {exc}"
        if self._memory_stage is not None:
            try:
                reset = getattr(self._memory_stage, "reset", None)
                if callable(reset):
                    reset()
                memory_status = "reset"
            except Exception as exc:  # noqa: BLE001 - cleanup boundary
                memory_status = f"error: {type(exc).__name__}: {exc}"
        cleanup = {
            "completed_at_ms": _now_ms(),
            "mapper": mapper_status,
            "memory": memory_status,
            "source_read_only": True,
            "worker_started": False,
            "simulator_used": False,
            "movement_control": False,
            "metrics_used": False,
            "recording_enabled": False,
        }
        self._mapper = None
        self._memory_stage = None
        return cleanup

    def _check_run_id_locked(self, action: str, run_id: str | None) -> None:
        if action in {"start", "validate", "reset"}:
            if run_id is not None and run_id != self._state.get("run_id"):
                raise ReplayActionError(
                    "run_id is stale for this workbench server",
                    status_code=409,
                    boundary="stale_run",
                    state=copy.deepcopy(self._state),
                )
            return
        current = self._state.get("run_id")
        if not run_id:
            raise ReplayActionError(
                f"{action} requires run_id",
                status_code=400,
                boundary="input",
                state=copy.deepcopy(self._state),
            )
        if run_id != current:
            raise ReplayActionError(
                "run_id is stale for this workbench server",
                status_code=409,
                boundary="stale_run",
                state=copy.deepcopy(self._state),
            )

    def _fresh_run_state(self, run_id: str) -> dict[str, Any]:
        state = self._initial_state()
        state["run_id"] = run_id
        return state

    def _initial_state(self) -> dict[str, Any]:
        return {
            "schema": WORKBENCH_STATE_SCHEMA,
            "server_identity": self._server_identity,
            "sequence_id": WORKBENCH_SEQUENCE_ID,
            "run_id": None,
            "phase": "idle",
            "source": None,
            "source_identity": None,
            "adapter": WORKBENCH_ADAPTER,
            "current_frame": None,
            "position": 0,
            "progress": {"completed": 0, "total": 0, "percent": 0.0},
            "summary": self._summary(frames_completed=0, frames_total=0),
            "machine_detail": self._machine_detail(),
            "perception": None,
            "observation": None,
            "memory": None,
            "timeline": [],
            "failure": None,
            "failure_boundary": None,
            "recovery_action": "start",
            "cleanup": None,
            "controls": self._controls(phase="idle"),
            "last_action": None,
        }

    def _controls(self, *, phase: str | None = None) -> dict[str, Any]:
        current_state = getattr(self, "_state", {})
        current_phase = phase or str(current_state.get("phase", "idle"))
        return {
            "cadence_ms": self._cadence_ms,
            "allowed_actions": _state_action_set(
                current_phase,
                feed=self._feed,
            ),
            "source_selection": {
                "kind": "image_directory",
                "editable": current_phase in {"idle", "completed", "failed", "cancelled"},
            },
        }

    def _summary(
        self,
        *,
        perception: PerceptionText | None = None,
        observation: Observation | None = None,
        memory: Any = None,
        duration_ms: int | float | None = None,
        frames_completed: int | None = None,
        frames_total: int | None = None,
    ) -> dict[str, Any]:
        progress = self._state.get("progress", {}) if hasattr(self, "_state") else {}
        summary = {
            "frames_completed": (
                int(progress.get("completed", 0))
                if frames_completed is None
                else int(frames_completed)
            ),
            "frames_total": (
                int(progress.get("total", 0))
                if frames_total is None
                else int(frames_total)
            ),
            "perception_status": perception.status if perception else None,
            "perception_things": len(perception.things) if perception else 0,
            "perception_signals": len(perception.signals) if perception else 0,
            "observation_available": observation is not None,
            "memory_health": memory.health if memory else None,
            "memory_records": memory.record_count if memory else 0,
            "last_duration_ms": round(float(duration_ms), 3) if duration_ms is not None else None,
        }
        return summary

    def _machine_detail(self) -> dict[str, Any]:
        return {
            "pipeline": {
                "perception_algorithm": DEFAULT_PERCEPTION_ALGORITHM,
                "memory_implementation": DEFAULT_MEMORY_IMPLEMENTATION,
                "observation_adapter": "autonomy.decision.observation.observation_from_perception",
                "decision_cycle": "autonomy.decision.cycle.DecisionCycle",
            },
            "source_contract": {
                "sequence_id": WORKBENCH_SEQUENCE_ID,
                "adapter": WORKBENCH_ADAPTER,
                "ordered": True,
                "absence_supported": True,
                "max_frames": self.max_frames,
                "max_image_bytes": self.max_image_bytes,
            },
            "side_effects": {
                "observation_only": True,
                "source_read_only": True,
                "worker": False,
                "simulator": False,
                "movement_control": False,
                "metrics": False,
                "recording": False,
            },
            "last_transition": None,
        }

    def _record_action_locked(self, action: str) -> None:
        item = {"action": action, "at_ms": _now_ms()}
        self._state["last_action"] = item
        detail = self._state.setdefault("machine_detail", self._machine_detail())
        detail["last_transition"] = item
        self._state["controls"] = self._controls()

    def _timeline_item(
        self,
        *,
        frame: ReplayFrame,
        result: Any,
        previous_memory: dict[str, Any] | None,
    ) -> dict[str, Any]:
        memory = result.memory.to_dict() if result.memory else None
        previous_ids = {
            str(item.get("record_id"))
            for item in (previous_memory or {}).get("records", [])
            if isinstance(item, dict)
        }
        current_ids = {
            str(item.get("record_id"))
            for item in (memory or {}).get("records", [])
            if isinstance(item, dict)
        }
        return {
            "frame": frame.to_dict(include_path=False),
            "perception": result.perception.to_dict() if result.perception else None,
            "observation": result.observation.to_dict() if result.observation else None,
            "memory": memory,
            "perception_status": result.perception.status if result.perception else None,
            "observation_id": result.observation.observation_id if result.observation else None,
            "memory_health": result.memory.health if result.memory else None,
            "memory_record_count": result.memory.record_count if result.memory else 0,
            "memory_effect": {
                "added": sorted(current_ids - previous_ids),
                "removed": sorted(previous_ids - current_ids),
                "retained": sorted(current_ids & previous_ids),
            },
            "duration_ms": result.duration_ms,
        }

    @staticmethod
    def _validate_cadence(value: int) -> int:
        try:
            cadence = int(value)
        except (TypeError, ValueError) as exc:
            raise ReplayActionError(
                "cadence_ms must be a nonnegative integer",
                status_code=400,
                boundary="input",
            ) from exc
        if cadence < 0 or cadence > 60_000:
            raise ReplayActionError(
                "cadence_ms must be between 0 and 60000",
                status_code=400,
                boundary="input",
            )
        return cadence


def _now_ms() -> int:
    return int(time.time() * 1000)


class WorkbenchServer:
    """Loopback-only HTTP boundary for one persistent replay runner."""

    def __init__(
        self,
        runner: ImageReplayRunner,
        *,
        host: str = WORKBENCH_HOST,
        port: int = 0,
    ) -> None:
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("workbench server must bind to a loopback address")
        self.runner = runner
        self.host = host
        self.preferred_port = int(port)
        self._httpd: _WorkbenchHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._started_at_ms: int | None = None
        self.runner.attach_server(self)

    @property
    def url(self) -> str | None:
        httpd = self._httpd
        if httpd is None:
            return None
        host, port = httpd.server_address[:2]
        display_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
        return f"http://{display_host}:{port}/"

    def start(self) -> "WorkbenchServer":
        if self._httpd is not None:
            return self
        try:
            httpd = _WorkbenchHTTPServer(
                (self.host, self.preferred_port),
                _WorkbenchHTTPHandler,
            )
        except OSError:
            if self.preferred_port == 0:
                raise
            httpd = _WorkbenchHTTPServer((self.host, 0), _WorkbenchHTTPHandler)
        httpd.workbench = self
        self._httpd = httpd
        self._started_at_ms = _now_ms()
        self._thread = threading.Thread(
            target=httpd.serve_forever,
            name=f"automa-workbench-http-{self.runner.server_identity[-8:]}",
            daemon=True,
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        httpd = self._httpd
        thread = self._thread
        if httpd is None:
            return
        httpd.shutdown()
        httpd.server_close()
        if thread is not None:
            thread.join(timeout=1.0)
        self._httpd = None
        self._thread = None
        self.runner.detach_server()

    def health_payload(self) -> dict[str, Any]:
        state = self.runner.state()
        return {
            "schema": WORKBENCH_SERVER_SCHEMA,
            "server_identity": self.runner.server_identity,
            "available": self._httpd is not None,
            "host": self.host,
            "port": self._httpd.server_address[1] if self._httpd else None,
            "url": self.url,
            "started_at_ms": self._started_at_ms,
            "sequence_id": WORKBENCH_SEQUENCE_ID,
            "run_id": state.get("run_id"),
            "phase": state.get("phase"),
            "persistent_across_terminal_state": True,
            "observation_only": True,
        }

    def state_payload(self) -> dict[str, Any]:
        state = self.runner.state()
        state["server"] = self.health_payload()
        return state

    def action_payload(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ReplayActionError(
                "action body must be a JSON object",
                status_code=400,
                boundary="input",
            )
        allowed = {"action", "run_id", "source_dir", "cadence_ms"}
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise ReplayActionError(
                f"unknown action fields: {', '.join(str(item) for item in unknown)}",
                status_code=400,
                boundary="input",
            )
        action = payload.get("action")
        if not isinstance(action, str) or not action.strip():
            raise ReplayActionError(
                "action must be a non-empty string",
                status_code=400,
                boundary="input",
            )
        run_id = payload.get("run_id")
        if run_id is not None and (
            not isinstance(run_id, str) or not run_id.strip()
        ):
            raise ReplayActionError(
                "run_id must be a non-empty string",
                status_code=400,
                boundary="input",
            )
        source_dir = payload.get("source_dir")
        if source_dir is not None and not isinstance(source_dir, str):
            raise ReplayActionError(
                "source_dir must be a path string",
                status_code=400,
                boundary="input",
            )
        cadence_ms = payload.get("cadence_ms")
        if cadence_ms is not None and (
            isinstance(cadence_ms, bool) or not isinstance(cadence_ms, int)
        ):
            raise ReplayActionError(
                "cadence_ms must be an integer",
                status_code=400,
                boundary="input",
            )
        try:
            state = self.runner.dispatch(
                action,
                run_id=run_id,
                source_dir=source_dir,
                cadence_ms=cadence_ms,
            )
        except ReplayActionError:
            raise
        except SourceValidationError as exc:
            raise ReplayActionError(
                str(exc),
                status_code=422,
                boundary="source",
                state=self.state_payload(),
            ) from exc
        return {
            "schema": WORKBENCH_ACTION_RESULT_SCHEMA,
            "ok": True,
            "action": action,
            "state": state,
        }


class _WorkbenchHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    workbench: WorkbenchServer


class _WorkbenchHTTPHandler(BaseHTTPRequestHandler):
    server: _WorkbenchHTTPServer

    def do_GET(self) -> None:
        request = urlparse(self.path)
        if request.path in {"/", "/index.html"}:
            self._serve_html()
            return
        if request.path == "/favicon.ico":
            self._send(204, b"", "image/x-icon", include_body=False)
            return
        if request.path in {"/api/health", "/api/status", "/api/state"}:
            if request.path == "/api/health":
                payload = self.server.workbench.health_payload()
            else:
                payload = self.server.workbench.state_payload()
            self._send_json(200, payload)
            return
        if request.path == "/api/frame":
            query = parse_qs(request.query, keep_blank_values=True)
            frame_id = _query_one(query, "frame_id")
            run_id = _query_one(query, "run_id")
            if run_id is not None:
                current_run_id = self.server.workbench.runner.state().get("run_id")
                if run_id != current_run_id:
                    self._send_json(
                        409,
                        _error_payload(
                            "stale_run",
                            "run_id is stale for this workbench server",
                            self.server.workbench.state_payload(),
                        ),
                    )
                    return
            frame = self.server.workbench.runner.frame_bytes(frame_id)
            if frame is None:
                self._send_json(
                    404,
                    _error_payload(
                        "frame",
                        "frame bytes are unavailable",
                        self.server.workbench.state_payload(),
                    ),
                )
                return
            body, content_type = frame
            self._send(200, body, content_type)
            return
        self._send_json(
            404,
            _error_payload("route", f"unknown route: {request.path}", None),
        )

    def do_HEAD(self) -> None:
        request = urlparse(self.path)
        if request.path in {"/", "/index.html"}:
            try:
                body = WORKBENCH_HTML_PATH.read_bytes()
            except OSError as exc:
                self._send_json(500, _error_payload("server", str(exc), None), include_body=False)
                return
            self._send(200, body, "text/html; charset=utf-8", include_body=False)
            return
        self.do_GET()

    def do_POST(self) -> None:
        request = urlparse(self.path)
        if request.path != "/api/action":
            self._send_json(
                404,
                _error_payload("route", f"unknown route: {request.path}", None),
            )
            return
        content_length = self.headers.get("Content-Length")
        try:
            size = int(content_length) if content_length is not None else -1
        except ValueError:
            size = -1
        if size < 0 or size > WORKBENCH_MAX_ACTION_BYTES:
            self._send_json(
                413,
                _error_payload(
                    "input",
                    f"request body must be between 0 and {WORKBENCH_MAX_ACTION_BYTES} bytes",
                    None,
                ),
            )
            return
        raw = self.rfile.read(size)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._send_json(400, _error_payload("input", f"invalid JSON: {exc}", None))
            return
        try:
            result = self.server.workbench.action_payload(payload)
        except ReplayActionError as exc:
            self._send_json(
                exc.status_code,
                _error_payload(exc.boundary, str(exc), exc.state or self.server.workbench.state_payload()),
            )
            return
        self._send_json(200, result)

    def _serve_html(self) -> None:
        try:
            body = WORKBENCH_HTML_PATH.read_bytes()
        except OSError as exc:
            self._send_json(500, _error_payload("server", str(exc), None))
            return
        self._send(200, body, "text/html; charset=utf-8")

    def _send_json(self, status: int, payload: dict[str, Any], *, include_body: bool = True) -> None:
        self._send(
            status,
            json.dumps(payload, indent=2, sort_keys=True).encode("utf-8"),
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
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'",
        )
        self.end_headers()
        if include_body:
            self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


def _query_one(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key)
    return values[0] if values else None


def _error_payload(
    boundary: str,
    message: str,
    state: dict[str, Any] | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": WORKBENCH_ERROR_SCHEMA,
        "ok": False,
        "boundary": boundary,
        "message": message,
        "recovery": "Inspect the returned state and use the allowed action.",
    }
    if state is not None:
        payload["state"] = state
    return payload


def run_workbench_replay(
    source_dir: str | os.PathLike[str],
    *,
    cadence_ms: int = WORKBENCH_DEFAULT_CADENCE_MS,
    max_frames: int = WORKBENCH_DEFAULT_MAX_FRAMES,
    host: str = WORKBENCH_HOST,
    port: int = 0,
    serve: bool = False,
    open_browser: bool = False,
    json_output: bool = False,
    output: TextIO | None = None,
) -> CommandResult:
    """Run one CLI replay, optionally keeping the loopback workbench alive."""

    if json_output and (serve or open_browser):
        return CommandResult(2, "--json cannot be combined with --serve or --open")
    if open_browser:
        serve = True
    server: WorkbenchServer | None = None
    displayed_url: str | None = None
    try:
        runner = ImageReplayRunner(
            source_dir,
            cadence_ms=cadence_ms,
            max_frames=max_frames,
        )
        if serve:
            server = WorkbenchServer(runner, host=host, port=port).start()
            displayed_url = server.url
        runner.start()
        if output is not None and serve:
            print(
                _format_workbench_status(
                    runner.state(),
                    server_url=displayed_url,
                    serving=True,
                ),
                file=output,
            )
        if open_browser and displayed_url:
            webbrowser.open(displayed_url)
        if serve:
            try:
                while True:
                    time.sleep(0.25)
            except KeyboardInterrupt:
                return CommandResult(0, "workbench server stopped")
        state = runner.wait()
        exit_code = 0 if state.get("phase") == "completed" else 2
        if json_output:
            return CommandResult(
                exit_code,
                json.dumps(state, indent=2, sort_keys=True),
            )
        return CommandResult(
            exit_code,
            _format_workbench_status(
                state,
                server_url=displayed_url,
                serving=False,
            ),
        )
    except (ReplayActionError, SourceValidationError) as exc:
        state = runner.state() if "runner" in locals() else None
        if json_output:
            payload = {
                "schema": WORKBENCH_ERROR_SCHEMA,
                "ok": False,
                "boundary": getattr(exc, "boundary", "input"),
                "message": str(exc),
            }
            if state is not None:
                payload["state"] = state
            return CommandResult(2, json.dumps(payload, indent=2, sort_keys=True))
        return CommandResult(2, f"Workbench replay failed: {exc}")
    finally:
        if server is not None:
            server.stop()


def _format_workbench_status(
    state: dict[str, Any],
    *,
    server_url: str | None,
    serving: bool,
) -> str:
    source = state.get("source") or {}
    progress = state.get("progress") or {}
    lines = [
        "automa perception-memory workbench",
        f"phase: {state.get('phase')}",
        f"sequence: {state.get('sequence_id')}",
        f"run_id: {state.get('run_id') or '(none)'}",
        f"source: {source.get('source_path') or source.get('path') or '(none)'}",
        f"progress: {progress.get('completed', 0)}/{progress.get('total', 0)}",
    ]
    if server_url:
        lines.append(f"workbench: {server_url}")
    if serving:
        lines.append("server: persistent loopback mode; press Ctrl-C to stop")
    failure = state.get("failure")
    if isinstance(failure, dict):
        lines.append(f"failure: {failure.get('message')}")
    return "\n".join(lines)


__all__ = [
    "ImageFeed",
    "ImageReplayRunner",
    "ReplayActionError",
    "ReplayFrame",
    "SourceValidationError",
    "WorkbenchServer",
    "WORKBENCH_ACTIONS",
    "WORKBENCH_DEFAULT_CADENCE_MS",
    "WORKBENCH_SEQUENCE_ID",
    "load_image_feed",
    "normalize_image_directory",
    "run_workbench_replay",
]
